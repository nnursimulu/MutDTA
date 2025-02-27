import time, os, subprocess
import submitit

import numpy as np

import torch
from torch import nn
import torch.distributed as dist
from torch.utils.data.distributed import DistributedSampler
from torch_geometric.loader import DataLoader


from src.utils.loader import Loader
from src.analysis.metrics import get_save_metrics

from src.train_test.training import train, test, CheckpointSaver
from src.train_test.utils import print_device_info

from src.utils import config as cfg

def init_node(args):    
    args.ngpus_per_node = torch.cuda.device_count()

    # find the common host name on all nodes
    cmd = 'scontrol show hostnames ' + os.getenv('SLURM_JOB_NODELIST')
    stdout = subprocess.check_output(cmd.split())
    host_name = stdout.decode().splitlines()[0] # first node is the host
    args.dist_url = f'tcp://{host_name}:{args.port}'

    # distributed parameters
    args.rank = int(os.getenv('SLURM_NODEID')) * args.ngpus_per_node
    args.world_size = int(os.getenv('SLURM_NNODES')) * args.ngpus_per_node
    
def init_dist_gpu(args):
    job_env = submitit.JobEnvironment()
    args.gpu = job_env.local_rank
    args.rank = job_env.global_rank

    # PyTorch calls to setup gpus for distributed training
    dist.init_process_group(backend='gloo', init_method=args.dist_url, 
                            world_size=args.world_size, rank=args.rank)
    
    torch.manual_seed(0)
    torch.cuda.manual_seed_all(0)
    np.random.seed(0)
    
    torch.cuda.set_device(args.gpu)
    # cudnn.benchmark = True # not needed since we include dropout layers
    dist.barrier()

    # # disabling printing if not master process:
    # import builtins as __builtin__
    # builtin_print = __builtin__.print

    # def print(*args, **kwargs):
    #     force = kwargs.pop('force', False)
    #     if (args.rank == 0) or force:
    #         builtin_print(*args, **kwargs)

    # __builtin__.print = print


# distributed training fn
def dtrain(args, unknown_args):
    # ==== initialize the node ====
    init_node(args)
    
    
    # ==== Set up distributed training environment ====
    init_dist_gpu(args)
    
    # TODO: update this to loop through all options.
    # only support for a single option for now:
    MODEL = args.model_opt[0]
    DATA = args.data_opt[0]
    FEATURE = args.feature_opt[0]
    EDGEW = args.edge_opt[0]
    ligand_feature = args.ligand_feature_opt[0]
    ligand_edge = args.ligand_edge_opt[0]
    
    media_save_p = f'{cfg.MEDIA_SAVE_DIR}/{DATA}/'
    MODEL_KEY = Loader.get_model_key(model=MODEL,data=DATA,pro_feature=FEATURE,edge=EDGEW,
                                     ligand_feature=ligand_feature, ligand_edge=ligand_edge,
                                     batch_size=args.batch_size*args.world_size,
                                     lr=args.learning_rate,dropout=args.dropout,
                                     n_epochs=args.num_epochs,
                                     pro_overlap=args.protein_overlap,
                                     fold=args.fold_selection)
    
    print(os.getcwd())
    print(MODEL_KEY)
    print(f'---------------- DATA OPT ----------------')
    print(f"             data_opt: {args.data_opt}")
    print(f"      protein_overlap: {args.protein_overlap}")
    print(f"       fold_selection: {args.fold_selection}\n")
    print(f"---------------- MODEL OPT ---------------")
    print(f"     Selected og_model_opt: {args.model_opt}")
    print(f"         Selected data_opt: {args.data_opt}")
    print(f" Selected feature_opt list: {args.feature_opt}")
    print(f"    Selected edge_opt list: {args.edge_opt}")
    print(f"           forced training: {args.train}\n")

    print(f"-------------- HYPERPARAMETERS -----------")
    print(f"            Learning rate: {args.learning_rate}")
    print(f"                  Dropout: {args.dropout}")
    print(f"             dropout_prot: {args.dropout_prot}")
    print(f"              pro_emb_dim: {args.pro_emb_dim}")
    print(f"               Num epochs: {args.num_epochs}\n")
    
    print(f"----------------- DISTRIBUTED ARGS -----------------")
    print(f"         Local Batch size: {args.batch_size}")
    print(f"        Global Batch size: {args.batch_size*args.world_size}")
    print(f"                      GPU: {args.gpu}")
    print(f"                     Rank: {args.rank}")
    print(f"               World Size: {args.world_size}")

    
    print(f'----------------- GPU INFO ------------------------')
    print_device_info(args.gpu)
    
    
    # ==== Load up training dataset ====
    loaders = Loader.load_distributed_DataLoaders(
        num_replicas=args.world_size, rank=args.rank, seed=args.rand_seed,
        data=DATA, pro_feature=FEATURE, edge_opt=EDGEW,
        batch_train=args.batch_size, # local batch size (per gpu)
        datasets=['train', 'test', 'val'],
        training_fold=args.fold_selection, # default is None from arg_parse
        protein_overlap=args.protein_overlap,
        ligand_feature=ligand_feature, ligand_edge=ligand_edge,
        num_workers=args.slurm_cpus_per_task, # number of subproc used for data loading
    )
    print(f"Data loaded")
    
    
    # ==== Load model ====
    # args.gpu is the local rank for this process
    model = Loader.init_model(model=MODEL, pro_feature=FEATURE, pro_edge=EDGEW, 
                              dropout=args.dropout, 
                              dropout_prot=args.dropout_prot, 
                              pro_emb_dim=args.pro_emb_dim,
                              **unknown_args).cuda(args.gpu)
    
    cp_saver = CheckpointSaver(model=model, save_path=f'{cfg.MODEL_SAVE_DIR}/{MODEL_KEY}.model',
                            train_all=False,
                            patience=1000, min_delta=(0.2 if DATA == cfg.DATA_OPT.PDBbind else 0.05),
                            dist_rank=args.rank)
    # load ckpnt
    ckpt_fp = cp_saver.save_path if os.path.exists(cp_saver.save_path) else cp_saver.save_path + '_tmp'
    if os.path.exists(ckpt_fp) and args.rank == 0:
        print('# Model already trained, loading checkpoint')
        model.safe_load_state_dict(torch.load(ckpt_fp, 
                                map_location=torch.device(f'cuda:{args.gpu}')))
        
    model = nn.SyncBatchNorm.convert_sync_batchnorm(model) # use if model contains batchnorm.
    model = nn.parallel.DistributedDataParallel(model, device_ids=[args.gpu], find_unused_parameters=False)
    
    torch.distributed.barrier() # Sync params across GPUs before training
    
    # ==== train ====
    print("starting training:")
    logs = train(model=model, train_loader=loaders['train'], val_loader=loaders['val'], 
          device=args.gpu, saver=cp_saver, epochs=args.num_epochs, lr_0=args.learning_rate)
    torch.distributed.barrier() # Sync params across GPUs
    
    cp_saver.save()
    
    
    # ==== Evaluate ====
    loss, pred, actual = test(model, loaders['test'], args.gpu)
    torch.distributed.barrier() # Sync params across GPUs
    if args.rank == 0:
        print("Test loss:", loss)
        get_save_metrics(actual, pred,
                    save_figs=False,
                    save_path=media_save_p,
                    model_key=MODEL_KEY,
                    csv_file=cfg.MODEL_STATS_CSV,
                    show=False,
                    logs=logs
                    )
        
    # validation
    loss, pred, actual = test(model, loaders['val'], args.gpu)
    torch.distributed.barrier() # Sync params across GPUs
    if args.rank == 0:
        print(f'# Val loss: {loss}')
        get_save_metrics(actual, pred,
                    save_figs=False,
                    save_path=media_save_p,
                    model_key=MODEL_KEY,
                    csv_file=cfg.MODEL_STATS_CSV_VAL,
                    show=False,
                    )
