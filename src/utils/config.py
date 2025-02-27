import os
# for huggingface models:
os.environ['TRANSFORMERS_CACHE'] = os.path.abspath('../hf_models/')
os.environ['HF_HOME'] = os.environ['TRANSFORMERS_CACHE'] 
os.environ['HF_HUB_OFFLINE'] = '1'
print("os.environ['TRANSFORMERS_CACHE'] - ",os.environ['TRANSFORMERS_CACHE'])
print("os.environ['HF_HOME'] - ",           os.environ['HF_HOME'])
print("os.environ['HF_HUB_OFFLINE'] - ",    os.environ['HF_HUB_OFFLINE'])

from prody import confProDy
confProDy(verbosity='none') # stop printouts from prody

from src.utils.enum import StringEnum
#############################
# Model and data options
#############################
# Datasets
class DATA_OPT(StringEnum):
    davis = 'davis'
    kiba = 'kiba'
    PDBbind = 'PDBbind'
    platinum = 'platinum'

# Model options
class MODEL_OPT(StringEnum):
    DG = 'DG'
    DGI = 'DGI'
    
    # ESM models:
    ED = 'ED'
    EDA = 'EDA'
    EDI = 'EDI'
    EDAI = 'EDAI'
    SPD = 'SPD' # SaProt
    
    # ChemGPT models
    CD = 'CD'
    CED = 'CED'
    
    RNG = 'RNG' # ring3DTA model
    
    GVP = 'GVP'
    GVPL = "GVPL" # GVP ligand branch only
    
    GVPL_RNG = "GVPL_RNG" # ring3DTA with GVP ligand branch
    GVPL_ESM = "GVPL_ESM"

# protein options
class PRO_EDGE_OPT(StringEnum):
    simple = 'simple'
    binary = 'binary'
    
    anm = 'anm'
    af2 = 'af2'
    af2_anm = 'af2_anm'
    ring3 = 'ring3'
    
    aflow = 'aflow' # alphaFlow confirmations
    aflow_ring3 = 'aflow_ring3'
    
class PRO_FEAT_OPT(StringEnum):
    nomsa = 'nomsa'
    msa = 'msa'
    shannon = 'shannon'
    
    foldseek = 'foldseek'
    gvp = 'gvp'
    
# Protein options that require PDB structure files to work
OPT_REQUIRES_MSA_ALN = StringEnum('msa', ['msa', 'shannon'])
OPT_REQUIRES_PDB = StringEnum('needs_structure', ['anm', 'af2', 'af2_anm', 'ring3', 
                                                  'aflow', 'aflow_ring3', 'foldseek',
                                                  'gvp'])
OPT_REQUIRES_CONF = StringEnum('multiple_pdb', ['af2', 'af2_anm', 'ring3', 'aflow',
                                                'aflow_ring3'])
OPT_REQUIRES_AFLOW_CONF = StringEnum('alphaflow_confs', ['aflow', 'aflow_ring3'])
OPT_REQUIRES_RING3 = StringEnum('ring3', ['ring3', 'aflow_ring3'])
OPT_REQUIRES_SDF = StringEnum('lig_sdf', ['gvp'])

# ligand options
class LIG_EDGE_OPT(StringEnum):
    binary = 'binary'

class LIG_FEAT_OPT(StringEnum):
    original = 'original'
    gvp = 'gvp'


#############################
# save paths
#############################
from pathlib import Path

# Model save paths
issue_number = None

DATA_BASENAME = f'data/{f"v{issue_number}" if issue_number else ""}'
RESULTS_PATH = os.path.abspath(f'results/{f"v{issue_number}/" if issue_number else ""}')
MEDIA_SAVE_DIR      = f'{RESULTS_PATH}/model_media/'
MODEL_STATS_CSV     = f'{RESULTS_PATH}/model_media/model_stats.csv'
MODEL_STATS_CSV_VAL = f'{RESULTS_PATH}/model_media/model_stats_val.csv'
MODEL_SAVE_DIR      = f'{RESULTS_PATH}/model_checkpoints/ours'
CHECKPOINT_SAVE_DIR = MODEL_SAVE_DIR # alias for clarity
os.makedirs(MODEL_SAVE_DIR, exist_ok=True)
os.makedirs(MEDIA_SAVE_DIR, exist_ok=True)

# cluster based configs:
import socket
DOMAIN_NAME = socket.getfqdn().split('.')
CLUSTER = DOMAIN_NAME[0]

SLURM_CONSTRAINT = None
SLURM_PARTITION = None
SLURM_ACCOUNT = None
SLURM_GPU_NAME = 'v100'
DATA_ROOT= os.path.abspath(f"../{DATA_BASENAME}/")

if ('uhnh4h' in DOMAIN_NAME) or ('h4h' in DOMAIN_NAME):
    CLUSTER = 'h4h'
    SLURM_PARTITION = 'gpu'
    SLURM_CONSTRAINT = 'gpu32g'
    SLURM_ACCOUNT = 'kumargroup_gpu'
elif 'graham' in DOMAIN_NAME:
    CLUSTER = 'graham'
    SLURM_CONSTRAINT = 'cascade,v100'
    DATA_ROOT = os.path.abspath(Path.home() / 'scratch' / DATA_BASENAME)
elif 'cedar' in DOMAIN_NAME:
    CLUSTER = 'cedar'
    SLURM_GPU_NAME = 'v100l'
    DATA_ROOT = os.path.abspath(Path.home() / 'scratch' / DATA_BASENAME)
elif 'narval' in DOMAIN_NAME:
    CLUSTER = 'narval'
    SLURM_GPU_NAME = 'a100'
    DATA_ROOT = os.path.abspath(Path.home() / 'scratch' / DATA_BASENAME)


# bin paths
FOLDSEEK_BIN = f'{Path.home()}/lib/foldseek/bin/foldseek'
MMSEQ2_BIN = f'{Path.home()}/lib/mmseqs/bin/mmseqs'
RING3_BIN = f'{Path.home()}/lib/ring-3.0.0/ring/bin/ring'

if 'uhnh4h' in DOMAIN_NAME:
    UniRef_dir = '/cluster/projects/kumargroup/sequence_databases/UniRef30_2020_06/UniRef30_2020_06'
    hhsuite_bin_dir = '/cluster/tools/software/centos7/hhsuite/3.3.0/bin'
else:
    UniRef_dir = '/cvmfs/bio.data.computecanada.ca/content/databases/Core/alphafold2_dbs/2024_01/uniref30/UniRef30_2021_03'
    hhsuite_bin_dir = '/cvmfs/soft.computecanada.ca/easybuild/software/2023/x86-64-v3/MPI/gcc12/openmpi4/hh-suite/3.3.0/bin'


###########################
# LOGGING STUFF:
# Adapted from - https://stackoverflow.com/questions/384076/how-can-i-color-python-logging-output
###########################

import logging 

class CustomFormatter(logging.Formatter):

    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    blue = "\x1b[36;20m"
    reset = "\x1b[0m"
    format = "%(asctime)s|%(name)s:%(levelname)s - %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: grey + format + reset,
        logging.INFO: blue + '%(message)s' + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

logger = logging.getLogger()
logger.setLevel(logging.WARNING)

# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)

ch.setFormatter(CustomFormatter())

logger.addHandler(ch)
