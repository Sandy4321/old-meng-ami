from collections import OrderedDict
import sys

import torch
from torch import nn
import torch.nn.init as nn_init
import torch.nn.functional as F
from torch.autograd import Variable



# CNN BASED MULTIDECODERS



# Simpler multidecoder design with convolutional encoder/decoder layers
class CNNMultidecoder(nn.Module):
    def __init__(self, freq_dim=80,
                       splicing=[5,5],
                       enc_channel_sizes=[],
                       enc_kernel_sizes=[],
                       enc_downsample_sizes=[],
                       enc_fc_sizes=[],
                       latent_dim=512,
                       dec_fc_sizes=[],
                       dec_channel_sizes=[],
                       dec_kernel_sizes=[],
                       dec_upsample_sizes=[],
                       activation="ReLU",
                       decoder_classes=[""],
                       use_batch_norm=False,
                       strided=False,
                       weight_init="xavier_uniform"):
        super(CNNMultidecoder, self).__init__()

        # Store initial parameters
        self.freq_dim = freq_dim
        self.splicing = splicing    # [left, right]
        self.time_dim = (sum(splicing) + 1)

        self.enc_channel_sizes = enc_channel_sizes
        self.enc_kernel_sizes = enc_kernel_sizes
        self.enc_downsample_sizes = enc_downsample_sizes
        self.enc_fc_sizes = enc_fc_sizes

        self.latent_dim = latent_dim

        self.dec_fc_sizes = dec_fc_sizes
        self.dec_channel_sizes = dec_channel_sizes
        self.dec_kernel_sizes = dec_kernel_sizes
        self.dec_upsample_sizes = dec_upsample_sizes

        self.activation = activation
        self.decoder_classes = decoder_classes
        self.use_batch_norm = use_batch_norm
        self.weight_init = weight_init

        # Replace pools with strided convolutions
        # See https://arxiv.org/pdf/1412.6806.pdf
        self.strided = strided


        # STEP 1: Construct encoder


        current_channels = 1
        current_height = self.time_dim
        current_width = self.freq_dim

        # Convolutional stage
        self.encoder_conv_layers = OrderedDict()
        for idx in range(len(enc_channel_sizes)):
            enc_channel_size = enc_channel_sizes[idx]
            enc_kernel_size = enc_kernel_sizes[idx]
            enc_downsample_size = enc_downsample_sizes[idx]

            if self.strided:
                # >1 stride only in frequency direction (i.e. stride 1 in time dimension)
                self.encoder_conv_layers["conv2d_%d" % idx] = nn.Conv2d(current_channels,
                                                                        enc_channel_size,
                                                                        enc_kernel_size,
                                                                        stride=(1, enc_downsample_size))
                self.init_weights(self.encoder_conv_layers["conv2d_%d" % idx], "conv2d")
                current_channels = enc_channel_size

                # Formula from http://pytorch.org/docs/master/nn.html#conv2d
                # Assumes padding = 0, dilation = 1
                current_height = (current_height - (enc_kernel_size - 1) - 1) + 1
                current_width = (current_width - (enc_kernel_size - 1) - 1) // enc_downsample_size + 1
                
                if self.use_batch_norm:
                    self.encoder_conv_layers["batchnorm2d_%d" % idx] = nn.BatchNorm2d(enc_channel_size)

                self.encoder_conv_layers["%s_%d" % (self.activation, idx)] = getattr(nn, self.activation)()
            else:
                self.encoder_conv_layers["conv2d_%d" % idx] = nn.Conv2d(current_channels,
                                                                        enc_channel_size,
                                                                        enc_kernel_size)
                self.init_weights(self.encoder_conv_layers["conv2d_%d" % idx], "conv2d")
                current_channels = enc_channel_size

                # Formula from http://pytorch.org/docs/master/nn.html#conv2d
                # Assumes stride = 1, padding = 0, dilation = 1
                current_height = (current_height - (enc_kernel_size - 1) - 1) + 1
                current_width = (current_width - (enc_kernel_size - 1) - 1) + 1
                
                if self.use_batch_norm:
                    self.encoder_conv_layers["batchnorm2d_%d" % idx] = nn.BatchNorm2d(enc_channel_size)

                self.encoder_conv_layers["%s_%d" % (self.activation, idx)] = getattr(nn, self.activation)()
                
                if enc_downsample_size > 0:
                    # Pool only in frequency direction (i.e. kernel and stride 1 in time dimension)
                    # Return indices as well (useful for unpooling: see
                    #   http://pytorch.org/docs/master/nn.html#maxunpool2d)
                    self.encoder_conv_layers["maxpool2d_%d" % idx] = nn.MaxPool2d((1, enc_downsample_size),
                                                                                  return_indices=True)
                    
                    # Formula from http://pytorch.org/docs/master/nn.html#maxpool2d 
                    # Assumes stride = enc_downsample_size (default), padding = 0, dilation = 1
                    current_height = current_height     # No change in time dimension!
                    current_width = int((current_width - (enc_downsample_size - 1) - 1) / enc_downsample_size) + 1

        self.encoder_conv = nn.Sequential(self.encoder_conv_layers)
        
        # Fully-connected stage
        self.encoder_fc_layers = OrderedDict()
        current_fc_dim = current_channels * current_height * current_width
        for idx in range(len(enc_fc_sizes)):
            enc_fc_size = enc_fc_sizes[idx]
            
            self.encoder_fc_layers["lin_%d" % idx] = nn.Linear(current_fc_dim, enc_fc_size)
            self.init_weights(self.encoder_fc_layers["lin_%d" % idx], self.activation)
            current_fc_dim = enc_fc_size

            if self.use_batch_norm:
                self.encoder_fc_layers["batchnorm1d_%d" % idx] = nn.BatchNorm1d(current_fc_dim)
            self.encoder_fc_layers["%s_%d" % (self.activation, idx)] = getattr(nn, self.activation)()            

        self.encoder_fc_layers["lin_final"] = nn.Linear(current_fc_dim, self.latent_dim)
        self.init_weights(self.encoder_fc_layers["lin_final"], self.activation)
        # self.encoder_fc_layers["%s_final" % self.activation] = getattr(nn, self.activation)()
        self.encoder_fc = nn.Sequential(self.encoder_fc_layers)


        # STEP 2: Construct decoders


        self.decoder_fc = dict()
        self.decoder_fc_layers = dict()
        self.decoder_deconv = dict()
        self.decoder_deconv_layers = dict()

        # Save values from encoder stage
        input_channels = current_channels
        input_height = current_height
        input_width = current_width

        for decoder_class in self.decoder_classes:
            # Fully-connected stage
            self.decoder_fc_layers[decoder_class] = OrderedDict()
            current_fc_dim = self.latent_dim
            for idx in range(len(dec_fc_sizes)):
                dec_fc_size = dec_fc_sizes[idx]
                
                self.decoder_fc_layers[decoder_class]["%s_%d" % (self.activation, idx)] = getattr(nn, self.activation)()

                self.decoder_fc_layers[decoder_class]["lin_%d" % idx] = nn.Linear(current_fc_dim, dec_fc_size)
                self.init_weights(self.decoder_fc_layers[decoder_class]["lin_%d" % idx], self.activation)
                current_fc_dim = dec_fc_size

                if self.use_batch_norm:
                    self.decoder_fc_layers[decoder_class]["batchnorm1d_%d" % idx] = nn.BatchNorm1d(current_fc_dim)

        
            self.decoder_fc_layers[decoder_class]["%s_final" % self.activation] = getattr(nn, self.activation)()
            self.decoder_fc_layers[decoder_class]["lin_final"] = nn.Linear(current_fc_dim,
                                                                           input_channels * input_height * input_width)
            self.init_weights(self.decoder_fc_layers[decoder_class]["lin_final"], self.activation)
            
            self.decoder_fc[decoder_class] = nn.Sequential(self.decoder_fc_layers[decoder_class])
            self.add_module("decoder_fc_%s" % decoder_class, self.decoder_fc[decoder_class])

            # Deconvolution stage
            current_height = input_height
            current_width = input_width

            self.decoder_deconv_layers[decoder_class] = OrderedDict()
            for idx in range(len(dec_channel_sizes)):
                dec_channel_size = dec_channel_sizes[idx]
                dec_kernel_size = dec_kernel_sizes[idx]
                dec_upsample_size = dec_upsample_sizes[idx]
                
                self.decoder_deconv_layers[decoder_class]["%s_%d" % (self.activation, idx)] = getattr(nn, self.activation)()

                if self.strided:
                    # Perform "fractional striding" to upsample
                    output_channels = 1 if idx == len(dec_channel_sizes) - 1 else dec_channel_sizes[idx + 1]

                    self.decoder_deconv_layers[decoder_class]["conv2d_%d" % idx] = nn.ConvTranspose2d(dec_channel_size,
                                                                            output_channels,
                                                                            dec_kernel_size,
                                                                            stride=(1, dec_upsample_size))
                    self.init_weights(self.decoder_deconv_layers[decoder_class]["conv2d_%d" % idx], "conv2d")

                    # Formula for length from http://pytorch.org/docs/master/nn.html#torch.nn.ConvTranspose2d
                    # Assumes padding = 0, dilation = 1
                    current_height = (current_height - 1) + dec_kernel_size
                    current_width = (current_width - 1) * dec_upsample_size + dec_kernel_size 

                    if self.use_batch_norm and idx != len(dec_channel_sizes) - 1:
                        # Don't normalize if it's the output layer!
                        self.decoder_deconv_layers[decoder_class]["batchnorm2d_%d" % idx] = nn.BatchNorm2d(output_channels)
                else:
                    if dec_upsample_size > 0:
                        # Un-pool only in frequency direction (i.e. kernel and stride 1 in time dimension)
                        self.decoder_deconv_layers[decoder_class]["maxunpool2d_%d" % idx] = nn.MaxUnpool2d((1, dec_upsample_size))
                        
                        # Formula from http://pytorch.org/docs/master/nn.html#maxunpool2d 
                        # Assumes stride = dec_upsample_size (default), padding = 0, dilation = 1
                        current_height = current_height     # No change in time dimension!
                        current_width = current_width * dec_upsample_size 

                    # Re-pad signal to "de-convolve"
                    # https://pgaleone.eu/neural-networks/2016/11/24/convolutional-autoencoders/
                    padding = dec_kernel_size - 1 
                    output_channels = 1 if idx == len(dec_channel_sizes) - 1 else dec_channel_sizes[idx + 1]
                    self.decoder_deconv_layers[decoder_class]["conv2d_%d" % idx] = nn.Conv2d(dec_channel_size,
                                                                            output_channels,
                                                                            dec_kernel_size,
                                                                            padding=padding)
                    self.init_weights(self.decoder_deconv_layers[decoder_class]["conv2d_%d" % idx], "conv2d")

                    # Formula for length from http://pytorch.org/docs/master/nn.html#conv2d
                    # Assumes stride = 1, dilation = 1
                    current_height = current_height + padding
                    current_width = current_width + padding

                    if self.use_batch_norm and idx != len(dec_channel_sizes) - 1:
                        # Don't normalize if it's the output layer!
                        self.decoder_deconv_layers[decoder_class]["batchnorm2d_%d" % idx] = nn.BatchNorm2d(output_channels)

            self.decoder_deconv[decoder_class] = nn.Sequential(self.decoder_deconv_layers[decoder_class])
            self.add_module("decoder_deconv_%s" % decoder_class, self.decoder_deconv[decoder_class])

    def init_weights(self, layer, layer_name):
        if "xavier" in self.weight_init:
            try:
                gain = nn_init.calculate_gain(layer_name.lower())
                getattr(nn_init, self.weight_init)(layer.weight, gain=gain)
            except ValueError:
                # Happens if layer type isn't supported yet -- just use default gain
                getattr(nn_init, self.weight_init)(layer.weight)
        else:
            getattr(nn_init, self.weight_init)(layer.weight)

    def decoder_parameters(self, decoder_class):
        # Get parameters for just a specific decoder
        if decoder_class not in self.decoder_classes:
            print("Decoder class \"%s\" not found in decoders: %s" % (decoder_class, self.decoder_classes),
                  flush=True)
            sys.exit(1)
        decoder_deconv_parameters = self.decoder_deconv[decoder_class].parameters()
        for param in decoder_deconv_parameters:
            yield param
        decoder_fc_parameters = self.decoder_fc[decoder_class].parameters()
        for param in decoder_fc_parameters:
            yield param

    def encoder_parameters(self):
        # Get parameters for just the encoder
        encoder_fc_parameters = self.encoder_fc.parameters()
        for param in encoder_fc_parameters:
            yield param
        encoder_conv_parameters = self.encoder_conv.parameters()
        for param in encoder_conv_parameters:
            yield param
    
    def encode(self, feats):
        if self.strided:
            # Need to go layer-by-layer to get input sizes to conv2d layers, so that
            # conv2d transpose layers can avoid explicit padding issues 
            conv_input_sizes = []
            conv_encoded = feats
            for i, (encoder_conv_layer_name, encoder_conv_layer) in enumerate(self.encoder_conv_layers.items()):
                if "conv2d" in encoder_conv_layer_name:
                    conv_input_sizes.append(conv_encoded.size())
                conv_encoded = encoder_conv_layer(conv_encoded)
            fc_input_size = conv_encoded.size()
            conv_encoded_vec = conv_encoded.view(conv_encoded.size()[0], -1)
            return (self.encoder_fc(conv_encoded_vec), fc_input_size, conv_input_sizes)
        else:
            # Need to go layer-by-layer to get pooling indices
            pooling_indices = []    
            unpool_sizes = []
            conv_encoded = feats
            for i, (encoder_conv_layer_name, encoder_conv_layer) in enumerate(self.encoder_conv_layers.items()):
                if "maxpool2d" in encoder_conv_layer_name:
                    unpool_sizes.append(conv_encoded.size())
                    conv_encoded, new_pooling_indices = encoder_conv_layer(conv_encoded)
                    pooling_indices.append(new_pooling_indices)
                else:
                    conv_encoded = encoder_conv_layer(conv_encoded)
            fc_input_size = conv_encoded.size()
            conv_encoded_vec = conv_encoded.view(conv_encoded.size()[0], -1)
            
            return (self.encoder_fc(conv_encoded_vec), fc_input_size, unpool_sizes, pooling_indices)

    def decode(self, z, decoder_class, fc_input_size, conv_input_sizes=None, unpool_sizes=None, pooling_indices=None):
        fc_decoded = self.decoder_fc[decoder_class](z)
        fc_decoded_mat = fc_decoded.view(fc_input_size) 

        if self.strided:
            # Need to go layer-by-layer to specify output sizes for conv2d transpose layers 
            output = fc_decoded_mat
            for i, (decoder_deconv_layer_name, decoder_deconv_layer) in enumerate(self.decoder_deconv_layers[decoder_class].items()):
                if "conv2d" in decoder_deconv_layer_name:
                    current_conv_input_size = conv_input_sizes.pop()
                    output = decoder_deconv_layer(output, output_size=current_conv_input_size)
                else:
                    output = decoder_deconv_layer(output)

            return output
        else:
            # Need to go layer-by-layer to insert pooling indices into unpooling layers
            output = fc_decoded_mat
            for i, (decoder_deconv_layer_name, decoder_deconv_layer) in enumerate(self.decoder_deconv_layers[decoder_class].items()):
                if "maxunpool2d" in decoder_deconv_layer_name:
                    current_pooling_indices = pooling_indices.pop()
                    current_unpool_size = unpool_sizes.pop()
                    output = decoder_deconv_layer(output,
                                                  current_pooling_indices,
                                                  output_size=current_unpool_size)
                else:
                    output = decoder_deconv_layer(output)

            return output
    
    def forward_decoder(self, feats, decoder_class):
        if self.strided:
            latent, fc_input_size, conv_input_sizes = self.encode(feats.view(-1,
                                                                  1,
                                                                  self.time_dim,
                                                                  self.freq_dim))
            return self.decode(latent, decoder_class, fc_input_size, conv_input_sizes=conv_input_sizes)
        else:
            latent, fc_input_size, unpool_sizes, pooling_indices = self.encode(feats.view(-1,
                                                                               1,
                                                                               self.time_dim,
                                                                               self.freq_dim))
            return self.decode(latent, decoder_class, fc_input_size, unpool_sizes=unpool_sizes, pooling_indices=pooling_indices)



# Multidecoder design with convolutional encoder/decoder layers that utilizes
# variational Bayes lower bound
class CNNVariationalMultidecoder(CNNMultidecoder):
    def __init__(self, freq_dim=80,
                       splicing=[5,5],
                       enc_channel_sizes=[],
                       enc_kernel_sizes=[],
                       enc_downsample_sizes=[],
                       enc_fc_sizes=[],
                       latent_dim=512,
                       dec_fc_sizes=[],
                       dec_channel_sizes=[],
                       dec_kernel_sizes=[],
                       dec_upsample_sizes=[],
                       activation="ReLU",
                       decoder_classes=[""],
                       use_batch_norm=False,
                       strided=False,
                       weight_init="xavier_uniform"):
        super(CNNVariationalMultidecoder, self).__init__(freq_dim=freq_dim,
                                                         splicing=splicing,
                                                         enc_channel_sizes=enc_channel_sizes,
                                                         enc_kernel_sizes=enc_kernel_sizes,
                                                         enc_downsample_sizes=enc_downsample_sizes,
                                                         enc_fc_sizes=enc_fc_sizes,
                                                         latent_dim=latent_dim,
                                                         dec_fc_sizes=dec_fc_sizes,
                                                         dec_channel_sizes=dec_channel_sizes,
                                                         dec_kernel_sizes=dec_kernel_sizes,
                                                         dec_upsample_sizes=dec_upsample_sizes,
                                                         activation=activation,
                                                         decoder_classes=decoder_classes,
                                                         use_batch_norm=use_batch_norm,
                                                         strided=strided,
                                                         weight_init=weight_init)


        # STEP 1: Overwrite end of Fully-connected Encoder stage
        self.encoder_fc_layers.popitem()    # Pop activation stage
        lin_final_key, lin_final = self.encoder_fc_layers.popitem()    # Pop fully-connected layer into latent space
        current_fc_dim = lin_final.weight.size()[1]     # In-channels
        self.encoder_fc = nn.Sequential(self.encoder_fc_layers)     # Rebuild encoder FC stage


        # STEP 2: Latent Gaussian parameters
        

        self.latent_mu_layers = OrderedDict()
        self.latent_mu_layers["lin"] = nn.Linear(current_fc_dim, self.latent_dim)
        self.init_weights(self.latent_mu_layers["lin"], "linear")
        self.latent_mu = nn.Sequential(self.latent_mu_layers)
        
        self.latent_logvar_layers = OrderedDict()
        self.latent_logvar_layers["lin"] = nn.Linear(current_fc_dim, self.latent_dim)
        self.init_weights(self.latent_logvar_layers["lin"], "linear")
        self.latent_logvar = nn.Sequential(self.latent_logvar_layers)


    
    def encode(self, feats):
        if self.strided:
            # Need to go layer-by-layer to get input sizes to conv2d layers, so that
            # conv2d transpose layers can avoid explicit padding issues 
            conv_input_sizes = []
            conv_encoded = feats
            for i, (encoder_conv_layer_name, encoder_conv_layer) in enumerate(self.encoder_conv_layers.items()):
                if "conv2d" in encoder_conv_layer_name:
                    conv_input_sizes.append(conv_encoded.size())
                conv_encoded = encoder_conv_layer(conv_encoded)
            fc_input_size = conv_encoded.size()
            conv_encoded_vec = conv_encoded.view(conv_encoded.size()[0], -1)
            pre_latent = self.encoder_fc(conv_encoded_vec)

            mu = self.latent_mu(pre_latent)
            logvar = self.latent_logvar(pre_latent)

            return (mu, logvar, fc_input_size, conv_input_sizes)
        else:
            # Need to go layer-by-layer to get pooling indices
            pooling_indices = []    
            unpool_sizes = []
            conv_encoded = feats
            for i, (encoder_conv_layer_name, encoder_conv_layer) in enumerate(self.encoder_conv_layers.items()):
                if "maxpool2d" in encoder_conv_layer_name:
                    unpool_sizes.append(conv_encoded.size())
                    conv_encoded, new_pooling_indices = encoder_conv_layer(conv_encoded)
                    pooling_indices.append(new_pooling_indices)
                else:
                    conv_encoded = encoder_conv_layer(conv_encoded)
            fc_input_size = conv_encoded.size()
            conv_encoded_vec = conv_encoded.view(conv_encoded.size()[0], -1)
            pre_latent = self.encoder_fc(conv_encoded_vec)

            mu = self.latent_mu(pre_latent)
            logvar = self.latent_logvar(pre_latent)
            
            return (mu, logvar, fc_input_size, unpool_sizes, pooling_indices)

    def reparameterize(self, mu, logvar):
        # Reparameterization trick from VAE paper
        # https://arxiv.org/abs/1312.6114
        if self.training:
            std = logvar.mul(0.5).exp_()
            eps = Variable(std.data.new(std.size()).normal_())
            return eps.mul(std).add_(mu)
        else:
            return mu

    def decode(self, z, decoder_class, fc_input_size, conv_input_sizes=[], unpool_sizes=[], pooling_indices=[]):
        fc_decoded = self.decoder_fc[decoder_class](z)
        fc_decoded_mat = fc_decoded.view(fc_input_size) 

        if self.strided:
            if self.strided:
                # Need to go layer-by-layer to specify output sizes for conv2d transpose layers 
                output = fc_decoded_mat
                for i, (decoder_deconv_layer_name, decoder_deconv_layer) in enumerate(self.decoder_deconv_layers[decoder_class].items()):
                    if "conv2d" in decoder_deconv_layer_name:
                        current_conv_input_size = conv_input_sizes.pop()
                        output = decoder_deconv_layer(output, output_size=current_conv_input_size)
                    else:
                        output = decoder_deconv_layer(output)

                return output
        else:
            # Need to go layer-by-layer to insert pooling indices into unpooling layers
            output = fc_decoded_mat
            for i, (decoder_deconv_layer_name, decoder_deconv_layer) in enumerate(self.decoder_deconv_layers[decoder_class].items()):
                if "maxunpool2d" in decoder_deconv_layer_name:
                    current_pooling_indices = pooling_indices.pop()
                    current_unpool_size = unpool_sizes.pop()
                    output = decoder_deconv_layer(output,
                                                  current_pooling_indices,
                                                  output_size=current_unpool_size)
                else:
                    output = decoder_deconv_layer(output)

            return output
    
    def forward_decoder(self, feats, decoder_class):
        if self.strided:
            mu, logvar, fc_input_size, conv_input_sizes = self.encode(feats.view(-1,
                                                                      1,
                                                                      self.time_dim,
                                                                      self.freq_dim))
            z = self.reparameterize(mu, logvar) 
            return (self.decode(z, decoder_class, fc_input_size, conv_input_sizes=conv_input_sizes),
                    mu,
                    logvar)
        else:
            mu, logvar, fc_input_size, unpool_sizes, pooling_indices = self.encode(feats.view(-1,
                                                                                   1,
                                                                                   self.time_dim,
                                                                                   self.freq_dim))
            z = self.reparameterize(mu, logvar) 
            return (self.decode(z, decoder_class, fc_input_size, unpool_sizes=unpool_sizes, pooling_indices=pooling_indices),
                    mu,
                    logvar)



# Includes an adversarial classifier for picking IHM vs SDM1
class CNNDomainAdversarialMultidecoder(CNNMultidecoder):
    def __init__(self, freq_dim=80,
                       splicing=[5,5],
                       enc_channel_sizes=[],
                       enc_kernel_sizes=[],
                       enc_downsample_sizes=[],
                       enc_fc_sizes=[],
                       latent_dim=512,
                       dec_fc_sizes=[],
                       dec_channel_sizes=[],
                       dec_kernel_sizes=[],
                       dec_upsample_sizes=[],
                       activation="ReLU",
                       decoder_classes=[""],
                       use_batch_norm=False,
                       strided=False,
                       weight_init="xavier_uniform",
                       domain_adv_fc_sizes=[],
                       domain_adv_activation="Sigmoid"):
        super(CNNDomainAdversarialMultidecoder, self).__init__(freq_dim=freq_dim,
                                                         splicing=splicing,
                                                         enc_channel_sizes=enc_channel_sizes,
                                                         enc_kernel_sizes=enc_kernel_sizes,
                                                         enc_downsample_sizes=enc_downsample_sizes,
                                                         enc_fc_sizes=enc_fc_sizes,
                                                         latent_dim=latent_dim,
                                                         dec_fc_sizes=dec_fc_sizes,
                                                         dec_channel_sizes=dec_channel_sizes,
                                                         dec_kernel_sizes=dec_kernel_sizes,
                                                         dec_upsample_sizes=dec_upsample_sizes,
                                                         activation=activation,
                                                         decoder_classes=decoder_classes,
                                                         use_batch_norm=use_batch_norm,
                                                         strided=strided,
                                                         weight_init=weight_init)
        
        self.domain_adv_fc_sizes = domain_adv_fc_sizes
        self.domain_adv_activation = domain_adv_activation

        # Construct domain_adversary
        # Simple linear classifier
        self.domain_adversary_layers = OrderedDict()
        current_dim = self.latent_dim
        for i in range(len(self.domain_adv_fc_sizes)):
            next_dim = self.domain_adv_fc_sizes[i]
            self.domain_adversary_layers["lin_%d" % i] = nn.Linear(current_dim, next_dim)
            self.domain_adversary_layers["%s_%d" % (self.domain_adv_activation, i)] = getattr(nn, self.domain_adv_activation)()
            current_dim = next_dim
        self.domain_adversary_layers["lin_final"] = nn.Linear(current_dim, 1)
        self.domain_adversary_layers["%s_final" % self.domain_adv_activation] = getattr(nn, self.domain_adv_activation)() 
        self.domain_adversary = nn.Sequential(self.domain_adversary_layers)

    def domain_adversary_parameters(self):
        # Get parameters for just the domain_adversary
        domain_adversary_parameters = self.domain_adversary.parameters()
        for param in domain_adversary_parameters:
            yield param



# Includes an adversarial classifier for detecting real vs. fake IHM (or SDM1) utterances
class CNNGANMultidecoder(CNNMultidecoder):
    def __init__(self, freq_dim=80,
                       splicing=[5,5],
                       enc_channel_sizes=[],
                       enc_kernel_sizes=[],
                       enc_downsample_sizes=[],
                       enc_fc_sizes=[],
                       latent_dim=512,
                       dec_fc_sizes=[],
                       dec_channel_sizes=[],
                       dec_kernel_sizes=[],
                       dec_upsample_sizes=[],
                       activation="ReLU",
                       decoder_classes=[""],
                       use_batch_norm=False,
                       strided=False,
                       weight_init="xavier_uniform",
                       gan_fc_sizes=[],
                       gan_activation="Sigmoid"):
        super(CNNGANMultidecoder, self).__init__(freq_dim=freq_dim,
                                                 splicing=splicing,
                                                 enc_channel_sizes=enc_channel_sizes,
                                                 enc_kernel_sizes=enc_kernel_sizes,
                                                 enc_downsample_sizes=enc_downsample_sizes,
                                                 enc_fc_sizes=enc_fc_sizes,
                                                 latent_dim=latent_dim,
                                                 dec_fc_sizes=dec_fc_sizes,
                                                 dec_channel_sizes=dec_channel_sizes,
                                                 dec_kernel_sizes=dec_kernel_sizes,
                                                 dec_upsample_sizes=dec_upsample_sizes,
                                                 activation=activation,
                                                 decoder_classes=decoder_classes,
                                                 use_batch_norm=use_batch_norm,
                                                 strided=strided,
                                                 weight_init=weight_init)
        
        self.gan_fc_sizes = gan_fc_sizes
        self.gan_activation = gan_activation

        # Construct adversary for each decoder class
        # Simple linear classifier
        self.gan_layers = dict()
        self.gans = dict()
        for decoder_class in self.decoder_classes:
            self.gan_layers[decoder_class] = OrderedDict()
            current_dim = self.freq_dim * self.time_dim
            for i in range(len(self.gan_fc_sizes)):
                next_dim = self.gan_fc_sizes[i]
                self.gan_layers[decoder_class]["lin_%d" % i] = nn.Linear(current_dim, next_dim)
                self.gan_layers[decoder_class]["%s_%d" % (self.gan_activation, i)] = getattr(nn, self.gan_activation)()
                current_dim = next_dim
            self.gan_layers[decoder_class]["lin_final"] = nn.Linear(current_dim, 1)
            self.gan_layers[decoder_class]["Sigmoid_final"] = nn.Sigmoid() 
            self.gans[decoder_class] = nn.Sequential(self.gan_layers[decoder_class])
            self.add_module("gan_%s" % decoder_class, self.gans[decoder_class])

    def gan_parameters(self, decoder_class):
        # Get parameters for just the adversary
        gan_parameters = self.gans[decoder_class].parameters()
        for param in gan_parameters:
            yield param

    def forward_gan(self, feats, decoder_class):
        return self.gans[decoder_class](feats.view((-1, self.time_dim * self.freq_dim)))
