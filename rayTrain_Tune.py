"""
 This is a tuning script for the raytune library.
 
 support for DDP is done by RayTune
   - This is done by increasing the number of workers in the ScalingConfig
   - For example the following would distribute inference across 2 GPUs (num_workers*resources_per_worker['GPU']):
       num_workers=2,
       use_gpu=True,  
       resources_per_worker={"CPU": 2, "GPU": 1},
    - 
"""

import random
import os
import tempfile

import torch

import ray
from ray.air import session # this session just comes from train._internal.session._session
from ray.train import ScalingConfig, Checkpoint
from ray.train.torch import TorchCheckpoint, TorchTrainer
from ray.tune.search.optuna import OptunaSearch


from src.utils.loader import Loader
from src.train_test.simple import simple_train, simple_eval
from src.utils import config as cfg

def train_func(config):
    # ============ Init Model ==============
    model = Loader.init_model(model=config["model"], pro_feature=config["feature_opt"],
                            pro_edge=config["edge_opt"],
                            # additional kwargs send to model class to handle
                            **config['architecture_kwargs']
                            )
    
    # prepare model with rayTrain (moves it to correct device and wraps it in DDP)
    model = ray.train.torch.prepare_model(model, parallel_strategy='ddp',
                                          parallel_strategy_kwargs={'find_unused_parameters':True})
    
    # ============ Load dataset ==============
    print("Loading Dataset")
    loaders = Loader.load_DataLoaders(data=config['dataset'], pro_feature=config['feature_opt'], 
                                      edge_opt=config['edge_opt'], 
                                      ligand_feature=config['lig_feat_opt'],
                                      ligand_edge=config['lig_edge_opt'],
                                      path=cfg.DATA_ROOT, 
                                      batch_train=config['batch_size'],
                                      datasets=['train', 'val'],
                                      training_fold=config['fold_selection'])
    
    # prepare dataloaders with rayTrain (adds DistributedSampler and moves to correct device)
    for k in loaders.keys():
        loaders[k] = ray.train.torch.prepare_data_loader(loaders[k])
    
    
    # ============= Simple training and eval loop =====================
    optimizer = torch.optim.Adam(model.parameters(), lr=config["lr"])
    save_checkpoint = config.get("save_checkpoint", False)
    
    for _ in range(config['epochs']):            
        try:
            # NOTE: no need to pass in device, rayTrain will handle that for us
            simple_train(model, optimizer, loaders['train'], epochs=1)  # Train the model
            loss = simple_eval(model, loaders['val'])  # Compute test accuracy
        except RuntimeError as e: # potential memory error
            print("RuntimeError:", e)
            ray.train.report({"loss": 100})
            break
        
            
        # Report metrics (and possibly a checkpoint) to ray        
        checkpoint = None
        if save_checkpoint:
            checkpoint_dir = tempfile.gettempdir()
            checkpoint_path = checkpoint_dir + "/model.checkpoint"
            torch.save(model.state_dict(), checkpoint_path)
            checkpoint = Checkpoint.from_directory(checkpoint_dir)
            
        ray.train.report({"loss": loss}, checkpoint=checkpoint)
    
    
if __name__ == "__main__":
    print("DATA_ROOT:", cfg.DATA_ROOT)
    print("os.environ['TRANSFORMERS_CACHE']", os.environ['TRANSFORMERS_CACHE'])
    print("Cuda support:", torch.cuda.is_available(),":", 
                            torch.cuda.device_count(), "devices")
    print("CUDA VERSION:", torch.__version__)
        
    search_space = {
        ## constants:
        "epochs": 20,
        "model": cfg.MODEL_OPT.GVPL_ESM,
                
        "dataset": cfg.DATA_OPT.PDBbind,
        "feature_opt": cfg.PRO_FEAT_OPT.nomsa,
        "edge_opt": cfg.PRO_EDGE_OPT.binary,
        "lig_feat_opt": cfg.LIG_FEAT_OPT.gvp,
        "lig_edge_opt": cfg.LIG_EDGE_OPT.binary,
        
        "fold_selection": 0,
        "save_checkpoint": False,
                
        ## hyperparameters to tune:
        "lr": ray.tune.loguniform(1e-5, 1e-3),
        "batch_size": ray.tune.choice([16, 32, 64, 128]), # local batch size
        
        # model architecture hyperparams
        "architecture_kwargs":{
            "dropout": ray.tune.uniform(0.0, 0.5),
            "output_dim":  ray.tune.choice([128, 256, 512]),
        },
    }
    if 'esm' in search_space['model'].lower():
        search_space['batch_size'] = ray.tune.choice([4,8,10])
        
    arch_kwargs = search_space['architecture_kwargs']
    if search_space['model'] == cfg.MODEL_OPT.GVPL:
        arch_kwargs["num_GVPLayers"]= ray.tune.choice([2, 3, 4])
    elif search_space['model'] == cfg.MODEL_OPT.GVPL_RNG:
        arch_kwargs["pro_emb_dim"]  = ray.tune.choice([64, 128, 256])
        arch_kwargs["nheads_pro"]   = ray.tune.choice([3, 4, 5])
    elif search_space['model'] == cfg.MODEL_OPT.GVPL_ESM:
        arch_kwargs["num_GVPLayers"]    = ray.tune.choice([2, 3, 4])
        arch_kwargs["pro_dropout_gnn"]  = ray.tune.uniform(0.0, 0.5)
        arch_kwargs["pro_extra_fc_lyr"] = ray.tune.choice([True, False])
        arch_kwargs["pro_emb_dim"]      = ray.tune.choice([128, 256, 320])

    
    # each worker is a node from the ray cluster.
    # WARNING: SBATCH GPU directive should match num_workers*GPU_per_worker
    # same for cpu-per-task directive
    scaling_config = ScalingConfig(num_workers=4, # number of ray actors to launch to distribute compute across
                                   use_gpu=True,  # default is for each worker to have 1 GPU (overrided by resources per worker)
                                   resources_per_worker={"CPU": 2, "GPU": 1},
                                   # trainer_resources={"CPU": 2, "GPU": 1},
                                   # placement_strategy="PACK", # place workers on same node
                                   )

    print('init Tuner')     
    tuner = ray.tune.Tuner(
        TorchTrainer(train_func),
        param_space={
            "train_loop_config": search_space,
            "scaling_config": scaling_config
            },
        tune_config=ray.tune.TuneConfig(
            metric="loss",
            mode="min",
            search_alg=OptunaSearch(), # using ray.tune.search.Repeater() could be useful to get multiple trials per set of params
                                       # would be even better if we could set trial-wise dependencies for a certain fold.
                                       # https://github.com/ray-project/ray/issues/33677
            num_samples=500,
        ),
    )

    results = tuner.fit()
