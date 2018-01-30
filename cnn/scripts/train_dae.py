import os
import random
import shutil
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.autograd import Variable
from torch.utils.data import DataLoader

sys.path.append("./")
sys.path.append("./cnn")
from cnn_md import CNNMultidecoder
from utils.hao_data import HaoDataset

# Uses some structure from https://github.com/pytorch/examples/blob/master/vae/main.py

# Set up features
feature_name = "fbank"
feat_dim = int(os.environ["FEAT_DIM"])
left_context = int(os.environ["LEFT_CONTEXT"])
right_context = int(os.environ["RIGHT_CONTEXT"])

freq_dim = feat_dim
time_dim = (left_context + right_context + 1)

# Read in parameters to use for our network
enc_channel_sizes = []
for res_str in os.environ["ENC_CHANNELS_DELIM"].split("_"):
    if len(res_str) > 0:
        enc_channel_sizes.append(int(res_str))
enc_kernel_sizes = []
for res_str in os.environ["ENC_KERNELS_DELIM"].split("_"):
    if len(res_str) > 0:
        enc_kernel_sizes.append(int(res_str))
enc_pool_sizes = []
for res_str in os.environ["ENC_POOLS_DELIM"].split("_"):
    if len(res_str) > 0:
        enc_pool_sizes.append(int(res_str))
enc_fc_sizes = []
for res_str in os.environ["ENC_FC_DELIM"].split("_"):
    if len(res_str) > 0:
        enc_fc_sizes.append(int(res_str))

latent_dim = int(os.environ["LATENT_DIM"])

dec_fc_sizes = []
for res_str in os.environ["DEC_FC_DELIM"].split("_"):
    if len(res_str) > 0:
        dec_fc_sizes.append(int(res_str))
dec_channel_sizes = []
for res_str in os.environ["DEC_CHANNELS_DELIM"].split("_"):
    if len(res_str) > 0:
        dec_channel_sizes.append(int(res_str))
dec_kernel_sizes = []
for res_str in os.environ["DEC_KERNELS_DELIM"].split("_"):
    if len(res_str) > 0:
        dec_kernel_sizes.append(int(res_str))
dec_pool_sizes = []
for res_str in os.environ["DEC_POOLS_DELIM"].split("_"):
    if len(res_str) > 0:
        dec_pool_sizes.append(int(res_str))

activation = os.environ["ACTIVATION_FUNC"]
decoder_classes = []
for res_str in os.environ["DECODER_CLASSES_DELIM"].split("_"):
    if len(res_str) > 0:
        decoder_classes.append(res_str)
use_batch_norm = True if os.environ["USE_BATCH_NORM"] == "true" else False
weight_init = os.environ["WEIGHT_INIT"]

# Set up training parameters
batch_size = int(os.environ["BATCH_SIZE"])
epochs = int(os.environ["EPOCHS"])
optimizer_name = os.environ["OPTIMIZER"]
learning_rate = float(os.environ["LEARNING_RATE"])
on_gpu = torch.cuda.is_available()
log_interval = 1000   # Log results once for this many batches during training

# Set up data files
training_scps = dict()
for decoder_class in decoder_classes:
    training_scp_name = os.path.join(os.environ["CURRENT_FEATS"], "%s-train-norm.blogmel.scp" % decoder_class)
    training_scps[decoder_class] = training_scp_name

dev_scps = dict()
for decoder_class in decoder_classes:
    dev_scp_name = os.path.join(os.environ["CURRENT_FEATS"], "%s-dev-norm.blogmel.scp" % decoder_class)
    dev_scps[decoder_class] = dev_scp_name

# Fix random seed for debugging
torch.manual_seed(1)
if on_gpu:
    torch.cuda.manual_seed(1)
random.seed(1)

# Set up noising
noise_ratio = float(os.environ["NOISE_RATIO"])
print("Noising %.3f%% of input features" % (noise_ratio * 100.0), flush=True)

# Construct autoencoder with our parameters
print("Constructing model...", flush=True)
model = CNNMultidecoder(freq_dim=freq_dim,
                        splicing=[left_context, right_context], 
                        enc_channel_sizes=enc_channel_sizes,
                        enc_kernel_sizes=enc_kernel_sizes,
                        enc_pool_sizes=enc_pool_sizes,
                        enc_fc_sizes=enc_fc_sizes,
                        latent_dim=latent_dim,
                        dec_fc_sizes=dec_fc_sizes,
                        dec_channel_sizes=dec_channel_sizes,
                        dec_kernel_sizes=dec_kernel_sizes,
                        dec_pool_sizes=dec_pool_sizes,
                        activation=activation,
                        use_batch_norm=use_batch_norm,
                        decoder_classes=decoder_classes,
                        weight_init=weight_init)
if on_gpu:
    model.cuda()
model_dir = os.environ["MODEL_DIR"]
checkpoint_path = os.path.join(model_dir, "best_cnn_dae_ratio%s_md.pth.tar" % str(noise_ratio))
print("Done constructing model.", flush=True)
print(model, flush=True)

# Set up loss function
def reconstruction_loss(recon_x, x):
    MSE = nn.MSELoss()(recon_x, x.view(-1, time_dim, freq_dim))
    return MSE



# TRAIN MULTIDECODER



# Set up optimizers for each decoder, as well as a shared encoder optimizer
decoder_optimizers = dict()
for decoder_class in decoder_classes:
    decoder_optimizers[decoder_class] = getattr(optim, optimizer_name)(model.decoder_parameters(decoder_class),
                                                               lr=learning_rate)
encoder_optimizer = getattr(optim, optimizer_name)(model.encoder_parameters(),
                                                   lr=learning_rate)

print("Setting up data...", flush=True)
loader_kwargs = {"num_workers": 1, "pin_memory": True} if on_gpu else {}

print("Setting up training datasets...", flush=True)
training_datasets = dict()
training_loaders = dict()
for decoder_class in decoder_classes:
    current_dataset = HaoDataset(training_scps[decoder_class],
                                 left_context=left_context,
                                 right_context=right_context,
                                 shuffle_utts=True,
                                 shuffle_feats=True)
    training_datasets[decoder_class] = current_dataset
    training_loaders[decoder_class] = DataLoader(current_dataset,
                                                 batch_size=batch_size,
                                                 shuffle=False,
                                                 **loader_kwargs)
    print("Using %d training features (%d batches) for class %s" % (len(current_dataset),
                                                                    len(training_loaders[decoder_class]),
                                                                    decoder_class),
          flush=True)

print("Setting up dev datasets...", flush=True)
dev_datasets = dict()
dev_loaders = dict()
for decoder_class in decoder_classes:
    current_dataset = HaoDataset(dev_scps[decoder_class],
                                 left_context=left_context,
                                 right_context=right_context,
                                 shuffle_utts=True,
                                 shuffle_feats=True)
    dev_datasets[decoder_class] = current_dataset
    dev_loaders[decoder_class] = DataLoader(current_dataset,
                                            batch_size=batch_size,
                                            shuffle=False,
                                            **loader_kwargs)
    print("Using %d dev features (%d batches) for class %s" % (len(current_dataset),
                                                               len(dev_loaders[decoder_class]),
                                                               decoder_class),
          flush=True)

# Set up minibatch shuffling (for training only) by decoder class
print("Setting up minibatch shuffling for training...", flush=True)
train_batch_counts = dict()
total_batches = 0
dev_batch_counts = dict()
for decoder_class in decoder_classes:
    train_batch_counts[decoder_class] = len(training_loaders[decoder_class])
    total_batches += train_batch_counts[decoder_class]
    
    dev_batch_counts[decoder_class] = len(dev_loaders[decoder_class])
print("%d total batches: %s" % (total_batches, str(train_batch_counts)), flush=True)

print("Done setting up data.", flush=True)

def train(epoch):
    model.train()
    train_loss = 0.0
    decoder_class_losses = {decoder_class: 0.0 for decoder_class in decoder_classes}

    batches_processed = 0
    current_train_batch_counts = train_batch_counts.copy()
    training_iterators = {decoder_class: iter(training_loaders[decoder_class]) for decoder_class in decoder_classes}

    while batches_processed < total_batches:
        # Pick a decoder class
        batch_idx = random.randint(0, total_batches - batches_processed - 1)
        for decoder_class in decoder_classes:
            current_count = current_train_batch_counts[decoder_class]
            if batch_idx < current_count:
                current_train_batch_counts[decoder_class] -= 1
                break
            else:
                batch_idx -= current_count

        # Get data for this decoder class
        feats, targets = training_iterators[decoder_class].next()
        feats = Variable(feats)
        targets = Variable(targets)
        if on_gpu:
            feats = feats.cuda()
            targets = targets.cuda()

        # Add noise to signal; randomly drop out % of elements
        noise_matrix = torch.FloatTensor(np.random.binomial(1, 1.0 - noise_ratio, size=feats.size()).astype(float))
        noise_matrix = Variable(noise_matrix)
        if on_gpu:
            noise_matrix = noise_matrix.cuda()
        noised_feats = torch.mul(feats, noise_matrix)

        # Backprop
        encoder_optimizer.zero_grad()
        decoder_optimizers[decoder_class].zero_grad()
        recon_batch = model.forward_decoder(noised_feats, decoder_class)
        loss = reconstruction_loss(recon_batch, targets)
        loss.backward()
        train_loss += loss.data[0]
        decoder_class_losses[decoder_class] += loss.data[0]
        decoder_optimizers[decoder_class].step()
        encoder_optimizer.step()

        batches_processed += 1
        if batches_processed % log_interval == 0:
            print("Train epoch %d: [%d/%d (%.1f%%)]\tLoss: %.6f" % (epoch,
                                                                    batches_processed,
                                                                    total_batches,
                                                                    100.0 * batches_processed / total_batches,
                                                                    train_loss / batches_processed),
                  flush=True)

    train_loss /= total_batches
    for decoder_class in decoder_classes:
        decoder_class_losses[decoder_class] /= train_batch_counts[decoder_class]

    return (train_loss, decoder_class_losses)

def test(epoch, loaders, noised=True):
    model.eval()
    test_loss = 0.0
    decoder_class_losses = {decoder_class: 0.0 for decoder_class in decoder_classes}

    batches_processed = 0
    for decoder_class in decoder_classes:
        for feats, targets in loaders[decoder_class]:
            # Set to volatile so history isn't saved (i.e., not training time)
            feats = Variable(feats, volatile=True)
            targets = Variable(targets, volatile=True)
            if on_gpu:
                feats = feats.cuda()
                targets = targets.cuda()

            if noised:
                # Add noise to signal; randomly drop out % of elements
                # Set to volatile so history on noise matrix isn't saved
                noise_matrix = torch.FloatTensor(np.random.binomial(1, 1.0 - noise_ratio, size=feats.size()).astype(float))
                noise_matrix = Variable(noise_matrix, volatile=True)
                if on_gpu:
                    noise_matrix = noise_matrix.cuda()
                noised_feats = torch.mul(feats, noise_matrix)
                recon_batch = model.forward_decoder(noised_feats, decoder_class)
            else:
                recon_batch = model.forward_decoder(feats, decoder_class)

            loss = reconstruction_loss(recon_batch, targets)
            test_loss += loss.data[0]
            decoder_class_losses[decoder_class] += loss.data[0]
            batches_processed += 1

    test_loss /= batches_processed
    for decoder_class in decoder_classes:
        batch_counts = dev_batch_counts if loaders == dev_loaders else train_batch_counts
        decoder_class_losses[decoder_class] /= batch_counts[decoder_class]

    return (test_loss, decoder_class_losses)

# Save model with best dev set loss thus far
best_dev_loss = float('inf')
save_best_only = True   # Set to False to always save model state, regardless of improvement

def save_checkpoint(state_obj, is_best, model_dir):
    if not save_best_only:
        filepath = os.path.join(model_dir, "ckpt_cnn_dae_ratio%s_md_%d.pth.tar" % (str(noise_ratio), state_obj["epoch"]))
        torch.save(state_obj, filepath)
        if is_best:
            shutil.copyfile(filepath, os.path.join(model_dir, "best_cnn_dae_ratio%s_md.pth.tar" % str(noise_ratio)))
    else:
        filepath = os.path.join(model_dir, "best_cnn_dae_ratio%s_md.pth.tar" % str(noise_ratio))
        torch.save(state_obj, filepath)

# Regularize via patience-based early stopping
max_patience = 3
epochs_since_improvement = 0

# 1-indexed for pretty printing
print("Starting training!", flush=True)
for epoch in range(1, epochs + 1):
    (train_loss, decoder_class_losses) = train(epoch)
    print("====> Epoch %d: Average train loss %.6f (%s)" % (epoch,
                                                            train_loss,
                                                            str(["%s: %.6f" % (dc, decoder_class_losses[dc]) for dc in decoder_classes])),
          flush=True)
    (dev_loss, decoder_class_losses) = test(epoch, dev_loaders)
    print("====> Dev set loss: %.6f (%s)" % (dev_loss,
                                             str(["%s: %.6f" % (dc, decoder_class_losses[dc]) for dc in decoder_classes])), 
          flush=True)
    
    is_best = (dev_loss <= best_dev_loss)
    if is_best:
        best_dev_loss = dev_loss
        epochs_since_improvement = 0
        print("New best dev set loss: %.6f" % best_dev_loss, flush=True)
    else:
        epochs_since_improvement += 1
        print("No improvement in %d epochs (best dev set loss: %.6f)" % (epochs_since_improvement, best_dev_loss),
              flush=True)
        if epochs_since_improvement >= max_patience:
            print("STOPPING EARLY", flush=True)
            break

    if not save_best_only or (save_best_only and is_best):
        # Save a checkpoint for our model!
        state_obj = {
            "epoch": epoch,
            "state_dict": model.state_dict(),
            "best_dev_loss": best_dev_loss,
            "dev_loss": dev_loss,
            "decoder_optimizers": {decoder_class: decoder_optimizers[decoder_class].state_dict() for decoder_class in decoder_classes},
            "encoder_optimizer": encoder_optimizer.state_dict(),
        }
        save_checkpoint(state_obj, is_best, model_dir)
        print("Saved checkpoint for model", flush=True)
    else:
        print("Not saving checkpoint; no improvement made", flush=True)

# Once done, load best checkpoint and determine reconstruction loss alone
print("Computing reconstruction loss...", flush=True)

# Load checkpoint (potentially trained on GPU) into CPU memory (hence the map_location)
checkpoint = torch.load(checkpoint_path, map_location=lambda storage,loc: storage)

# Set up model state and set to eval mode (i.e. disable batch norm)
model.load_state_dict(checkpoint["state_dict"])
model.eval()
print("Loaded checkpoint; best model ready now.")

train_loss, decoder_class_losses = test(epoch, training_loaders, noised=False)
print("====> Training set reconstruction loss w/o noising: %.6f (%s)" % (train_loss,
                                                             str(["%s: %.6f" % (dc, decoder_class_losses[dc]) for dc in decoder_classes])),
      flush=True)
dev_loss, decoder_class_losses = test(epoch, dev_loaders, noised=False)
print("====> Dev set reconstruction loss w/o noising: %.6f (%s)" % (dev_loss,
                                                        str(["%s: %.6f" % (dc, decoder_class_losses[dc]) for dc in decoder_classes])),
      flush=True)