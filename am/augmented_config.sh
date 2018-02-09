# For training
export START_EPOCH=1
export END_EPOCH=20

# For prediction
export MODEL_EPOCH=20

export MODEL_TYPE=ae
export NOISE_RATIO=0.25
export DATASET_NAME=ami-0.1
export DATASET=$MENG_ROOT/${DATASET_NAME}

# For adversarial multidecoders
export ADV_FC=( 256 )
export ADV_ACTIVATION=Sigmoid
export ADV_FC_DELIM=$(printf "_%s" "${ADV_FC[@]}")

export CNN_NAME=ENC_C_256_256_K_3_3_P_3_3_F_2048_2048/LATENT_128/DEC_F_2048_2048_C_256_256_K_3_3_P_3_3/ACT_SELU_BN_false_WEIGHT_INIT_xavier_uniform/OPT_Adam_LR_0.0001_EPOCHS_25_BATCH_256_DEBUG_false
# export AUGMENTED_DATA_DIR=${SCRATCH}/augmented_data/cnn/$DATASET_NAME/$CNN_NAME/${MODEL_TYPE}_ratio${NOISE_RATIO}
export AUGMENTED_DATA_DIR=${SCRATCH}/augmented_data/cnn/$DATASET_NAME/$CNN_NAME/adversarial_fc_${ADV_FC_DELIM}_act_${ADV_ACTIVATION}_${MODEL_TYPE}_ratio${NOISE_RATIO}

export SOURCE_DOMAIN=ihm
export TARGET_DOMAIN=ihm
export PREDICT_DOMAIN=ihm
export GOLD_DIR=/data/sls/scratch/haotang/ami/sls-data/${DATASET_NAME}

# if [ "$TRAIN_DOMAIN" = ihm ] ; then
#     export NPRED=3984
# else
#     # SDM1
#     export NPRED=3966
# fi

# Always use IHM pdfids! (See Hao email from 1/17/18)
export NPRED=3984

export EXPT_NAME="train_${TARGET_DOMAIN}/augmented_src_${SOURCE_DOMAIN}/frame-tdnn-450x7-step0.05/${CNN_NAME}"

export MODEL_DIR=$MODELS/am/$DATASET_NAME/$EXPT_NAME
mkdir -p $MODEL_DIR

export LOGS=${MENG_ROOT}/am/logs/$DATASET_NAME
mkdir -p $LOGS
