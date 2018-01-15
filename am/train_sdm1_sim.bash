#!/bin/bash
#SBATCH -p sm
#SBATCH -n1
#SBATCH -N1-1
#SBATCH -c 4
#SBATCH --gres=gpu:1
#SBATCH --mem=32768
#SBATCH --time=48:00:00
#SBATCH -J train_sdm1_sim
#SBATCH --exclude=sls-sm-[1,2,4]

echo "STARTING SDM1 SIMULATED ACOUSTIC MODEL TRAINING JOB"

. ./path.sh
. $MENG_ROOT/am/sdm1_sim_config.sh
. $MENG_ROOT/am/path-cuda.sh

mkdir -p $LOGS/$EXPT_NAME

for epoch in $(seq $START_EPOCH $END_EPOCH); do
    echo "========== EPOCH $epoch =========="

    epoch_log=$LOGS/$EXPT_NAME/train_sdm1_sim-epoch${epoch}.log

    if [ ! -f $MODEL_DIR/param-$((epoch-1)) ]; then
        # Parameter file doesn't exist -- only generate if we're just starting
        if [ "$epoch" -eq "1" ]; then
            echo "TDNN not initialized. Initializing parameters..."
            $MENG_ROOT/am/init-tdnn.py random > $MODEL_DIR/param-$((epoch-1))
            echo "Done initializing parameters."
        else
            echo "Parameter file does not exist for (epoch - 1 = $((epoch-1)))"
            exit 1
        fi
    fi

    OMP_NUM_THREADS=1 /data/sls/scratch/haotang/ami/dist/nn-20171210-5b69f7f/nnbin/frame-tdnn-learn-gpu \
        --frame-scp $AUGMENTED_DATA_DIR/train-src_ihm-tar_sdm1.scp \
        --label-scp $DATASET/sdm1-train-tri3.bali.scp \
        --param $MODEL_DIR/param-$((epoch-1)) \
        --opt-data $MODEL_DIR/opt-data-$((epoch-1)) \
        --output-param $MODEL_DIR/param-$epoch \
        --output-opt-data $MODEL_DIR/opt-data-$epoch \
        --label $DATASET/sdm1-pdfids.txt \
        --seed $epoch \
        --shuffle \
        --opt const-step \
        --step-size 0.05 \
        --clip 5 \
        > $epoch_log
done

echo "DONE SDM1 SIMULATED ACOUSTIC MODEL TRAINING JOB"
