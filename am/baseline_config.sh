# For training
export START_EPOCH=1
export END_EPOCH=20

# For prediction
export MODEL_EPOCH=20

export DEBUG_MODEL=false
if [ "$DEBUG_MODEL" = true ] ; then
    export DATASET_NAME=debug
    export DATASET=$TEST_FEATS
else
    # export DATASET_NAME=ami-0.1
    # export DATASET=/data/sls/scratch/haotang/ami/sls-data/${DATASET_NAME}
    export DATASET_NAME=ami-0.1
    export DATASET=$MENG_ROOT/${DATASET_NAME}
fi

export TRAIN_DOMAIN=ihm
export PREDICT_DOMAIN=ihm
export GOLD_DIR=/data/sls/scratch/haotang/ami/sls-data/${DATASET_NAME}

export EXPT_NAME="${DATASET_NAME}/train_${TRAIN_DOMAIN}/baseline/frame-tdnn-450x7-step0.05"

export MODEL_DIR=$MODELS/am/$EXPT_NAME
mkdir -p $MODEL_DIR

export LOGS=${MENG_ROOT}/am/logs
mkdir -p $LOGS
