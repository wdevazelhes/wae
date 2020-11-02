import numpy as np
import tensorflow as tf
import ops
from datahandler import datashapes
import tensorflow as tf
import random
import numpy as np



import tensorflow.compat.v1 as tf
tf.disable_v2_behavior()

tf.set_random_seed(0)
random.seed(0)
np.random.seed(0)

def encoder(opts, inputs, reuse=False, is_training=False):

    if opts['e_noise'] == 'add_noise':
        # Particular instance of the implicit random encoder
        def add_noise(x):
            shape = tf.shape(x)
            return x + tf.truncated_normal(shape, 0.0, 0.01, seed=0)
        def do_nothing(x):
            return x
        inputs = tf.cond(is_training,
                         lambda: add_noise(inputs), lambda: do_nothing(inputs))
    num_units = opts['e_num_filters']
    num_layers = opts['e_num_layers']

    with tf.variable_scope("encoder", reuse=reuse):
        if opts['e_arch'] == 'mlp':
            # Encoder uses only fully connected layers with ReLus
            hi = inputs
            i = 0
            for i in range(num_layers):
                hi = ops.linear(opts, hi, num_units, scope='h%d_lin' % i)
                if opts['batch_norm']:
                    hi = ops.batch_norm(opts, hi, is_training,
                                        reuse, scope='h%d_bn' % i)
                hi = tf.nn.relu(hi)
            if opts['e_noise'] != 'gaussian':
                res = ops.linear(opts, hi, opts['zdim'], 'hfinal_lin')
            else:
                mean = ops.linear(opts, hi, opts['zdim'], 'mean_lin')
                log_sigmas = ops.linear(opts, hi,
                                        opts['zdim'], 'log_sigmas_lin')
                res = (mean, log_sigmas)
        elif opts['e_arch'] == 'dcgan':
            # Fully convolutional architecture similar to DCGAN
            res, l1, l2, l3, l4, to_monitor = dcgan_encoder(opts, inputs, is_training, reuse)
        elif opts['e_arch'] == 'ali':
            # Architecture smilar to "Adversarially learned inference" paper
            res = ali_encoder(opts, inputs, is_training, reuse)
        elif opts['e_arch'] == 'began':
            # Architecture similar to the BEGAN paper
            res = began_encoder(opts, inputs, is_training, reuse)
        else:
            raise ValueError('%s Unknown encoder architecture' % opts['e_arch'])

        noise_matrix = None

        if opts['e_noise'] == 'implicit':
            # We already encoded the picture X -> res = E_1(X)
            # Now we return res + A(res) * eps, which is supposed
            # to project a noise on the directions depending on the
            # place in latent space
            sample_size = tf.shape(res)[0]
            eps = tf.random_normal((sample_size, opts['zdim']),
                                   0., 1., dtype=tf.float32, seed=0)
            eps_mod, noise_matrix = transform_noise(opts, res, eps)
            res = res + eps_mod

        if opts['pz'] == 'sphere':
            # Projecting back to the sphere
            res = tf.nn.l2_normalize(res, dim=1)
        elif opts['pz'] == 'uniform':
            # Mapping back to the [-1,1]^zdim box
            res = tf.nn.tanh(res)

        return res, l1, l2, l3, l4, to_monitor, noise_matrix

def decoder(opts, noise, reuse=False, is_training=True):
    assert opts['dataset'] in datashapes, 'Unknown dataset!'
    output_shape = datashapes[opts['dataset']]
    num_units = opts['g_num_filters']

    with tf.variable_scope("generator", reuse=reuse):
        if opts['g_arch'] == 'mlp':
            # Architecture with only fully connected layers and ReLUs
            layer_x = noise
            i = 0
            for i in range(opts['g_num_layers']):
                layer_x = ops.linear(opts, layer_x, num_units, 'h%d_lin' % i)
                layer_x = tf.nn.relu(layer_x)
                if opts['batch_norm']:
                    layer_x = ops.batch_norm(
                        opts, layer_x, is_training, reuse, scope='h%d_bn' % i)
            out = ops.linear(opts, layer_x,
                             np.prod(output_shape), 'h%d_lin' % (i + 1))
            out = tf.reshape(out, [-1] + list(output_shape))
            if opts['input_normalize_sym']:
                return tf.nn.tanh(out), out
            else:
                return tf.nn.sigmoid(out), out
        elif opts['g_arch'] in ['dcgan', 'dcgan_mod']:
            # Fully convolutional architecture similar to DCGAN
            res1, res2, ld1, ld2, ld3, ld4, noo, to_monitor_dec = dcgan_decoder(opts, noise, is_training, reuse)
        elif opts['g_arch'] == 'ali':
            # Architecture smilar to "Adversarially learned inference" paper
            res = ali_decoder(opts, noise, is_training, reuse)
        elif opts['g_arch'] == 'began':
            # Architecture similar to the BEGAN paper
            res = began_decoder(opts, noise, is_training, reuse)
        else:
            raise ValueError('%s Unknown decoder architecture' % opts['g_arch'])

        return res1, res2, ld1, ld2, ld3, ld4, noo, to_monitor_dec

def dcgan_encoder(opts, inputs, is_training=False, reuse=False):
    num_units = opts['e_num_filters']
    num_layers = opts['e_num_layers']
    layer_x = inputs
    to_monitor = dict()
    for i in range(num_layers):
        scale = 2**(num_layers - i - 1)
        layer_x, w, b = ops.conv2d(opts, layer_x, num_units // scale,
                             scope='h%d_conv' % i)
        # layer_x = tf.layers.Conv2D(num_units/scale, (5, 5), strides=(2, 2), 
        #                            kernel_initializer=tf.truncated_normal_initializer(stddev=opts['init_std'], seed=0), 
        #                            bias_initializer=tf.constant_initializer(opts['init_bias']), padding='SAME')(layer_x)
        to_monitor['we{}'.format(i)] = w
        to_monitor['be{}'.format(i)] = b
        if i == 0:
            l1 = layer_x
        if i == 1:
            l2 = layer_x
        if i == 2: 
            l3 = layer_x
        if i == 3: 
            l4 = layer_x
        if opts['batch_norm']:
            layer_x = tf.keras.layers.BatchNormalization(epsilon=opts['batch_norm_eps'], 
            # layer_x = tf.keras.layers.BatchNormalization(epsilon=opts['batch_norm_eps'], 
                                    momentum=opts['batch_norm_decay'], 
                                    fused=False, name='h%d_bn'%i)(layer_x, training=is_training)
                                    # forcing is_training to True doesn't change anything

            # layer_x = ops.batch_norm(opts, layer_x, is_training,
                                    #  reuse, scope='h%d_bn' % i)
            if i == 0:
                to_monitor['l1_bn'] = layer_x
        layer_x = tf.nn.relu(layer_x)
    if opts['e_noise'] != 'gaussian':
        res = ops.linear(opts, layer_x, opts['zdim'], scope='hfinal_lin')
        return res, l1, l2, l3, l4, to_monitor
    else:
        mean = ops.linear(opts, layer_x, opts['zdim'], scope='mean_lin')
        log_sigmas = ops.linear(opts, layer_x,
                                opts['zdim'], scope='log_sigmas_lin')
        return mean, log_sigmas

def ali_encoder(opts, inputs, is_training=False, reuse=False):
    num_units = opts['e_num_filters']
    layer_params = []
    layer_params.append([5, 1, num_units / 8])
    layer_params.append([4, 2, num_units / 4])
    layer_params.append([4, 1, num_units / 2])
    layer_params.append([4, 2, num_units])
    layer_params.append([4, 1, num_units * 2])
    # For convolution: (n - k) / stride + 1 = s
    # For transposed: (s - 1) * stride + k = n
    layer_x = inputs
    height = layer_x.get_shape()[1]
    width = layer_x.get_shape()[2]
    assert height == width
    for i, (kernel, stride, channels) in enumerate(layer_params):
        height = (height - kernel) / stride + 1
        width = height
        layer_x = ops.conv2d(
            opts, layer_x, channels, d_h=stride, d_w=stride,
            scope='h%d_conv' % i, conv_filters_dim=kernel, padding='VALID')
        if opts['batch_norm']:
            layer_x = ops.batch_norm(opts, layer_x, is_training,
                                     reuse, scope='h%d_bn' % i)
        layer_x = ops.lrelu(layer_x, 0.1)
    assert height == 1
    assert width == 1

    # Then two 1x1 convolutions.
    layer_x = ops.conv2d(opts, layer_x, num_units * 2, d_h=1, d_w=1,
                         scope='conv2d_1x1', conv_filters_dim=1)
    if opts['batch_norm']:
        layer_x = ops.batch_norm(opts, layer_x, is_training,
                                 reuse, scope='hfinal_bn')
    layer_x = ops.lrelu(layer_x, 0.1)
    layer_x = ops.conv2d(opts, layer_x, num_units / 2, d_h=1, d_w=1,
                         scope='conv2d_1x1_2', conv_filters_dim=1)

    if opts['e_noise'] != 'gaussian':
        res = ops.linear(opts, layer_x, opts['zdim'], scope='hlast_lin')
        return res
    else:
        mean = ops.linear(opts, layer_x, opts['zdim'], scope='mean_lin')
        log_sigmas = ops.linear(opts, layer_x,
                                opts['zdim'], scope='log_sigmas_lin')
        return mean, log_sigmas

def began_encoder(opts, inputs, is_training=False, reuse=False):
    num_units = opts['e_num_filters']
    assert num_units == opts['g_num_filters'], \
        'BEGAN requires same number of filters in encoder and decoder'
    num_layers = opts['e_num_layers']
    layer_x = ops.conv2d(opts, inputs, num_units, scope='hfirst_conv')
    for i in range(num_layers):
        if i % 3 < 2:
            if i != num_layers - 2:
                ii = i - (i / 3)
                scale = (ii + 1 - ii / 2)
            else:
                ii = i - (i / 3)
                scale = (ii - (ii - 1) / 2)
            layer_x = ops.conv2d(opts, layer_x, num_units * scale, d_h=1, d_w=1,
                                 scope='h%d_conv' % i)
            layer_x = tf.nn.elu(layer_x)
        else:
            if i != num_layers - 1:
                layer_x = ops.downsample(layer_x, scope='h%d_maxpool' % i,
                                         reuse=reuse)
    # Tensor should be [N, 8, 8, filters] at this point
    if opts['e_noise'] != 'gaussian':
        res = ops.linear(opts, layer_x, opts['zdim'], scope='hfinal_lin')
        return res
    else:
        mean = ops.linear(opts, layer_x, opts['zdim'], scope='mean_lin')
        log_sigmas = ops.linear(opts, layer_x,
                                opts['zdim'], scope='log_sigmas_lin')
        return mean, log_sigmas


def dcgan_decoder(opts, noise, is_training=False, reuse=False):
    output_shape = datashapes[opts['dataset']]
    num_units = opts['g_num_filters']
    batch_size = tf.shape(noise)[0]
    num_layers = opts['g_num_layers']
    to_monitor_dec = dict()
    if opts['g_arch'] == 'dcgan':
        height = output_shape[0] / 2**num_layers
        width = output_shape[1] / 2**num_layers
    elif opts['g_arch'] == 'dcgan_mod':
        height = output_shape[0] // 2**(num_layers - 1)
        width = output_shape[1] // 2**(num_layers - 1)
    noo = noise
    h0, w, b = ops.linear(
        opts, noise, num_units * height * width, scope='h0_lin', return_weights=True)
    to_monitor_dec['wd{}'.format(0)] = w
    to_monitor_dec['bd{}'.format(0)] = b
    ld1 = h0
    h0 = tf.reshape(h0, [-1, height, width, num_units])
    h0 = tf.nn.relu(h0)
    layer_x = h0
    for i in range(num_layers - 1):
        scale = 2**(i + 1)
        _out_shape = [batch_size, height * scale,
                      width * scale, num_units // scale]
        layer_x, w, b = ops.deconv2d(opts, layer_x, _out_shape,
                               scope='h%d_deconv' % i)
        to_monitor_dec['wd{}'.format(i+1)] = w
        to_monitor_dec['bd{}'.format(i+1)] = b
        if i == 0:
            ld2 = layer_x
        if i == 1:
            ld3 = layer_x
        if i == 2: 
            ld4 = layer_x
        if opts['batch_norm']:
            layer_x = ops.batch_norm(opts, layer_x,
                                     is_training, reuse, scope='h%d_bn' % i)
        layer_x = tf.nn.relu(layer_x)
    _out_shape = [batch_size] + list(output_shape)
    if opts['g_arch'] == 'dcgan':
        last_h, _, _ = ops.deconv2d(
            opts, layer_x, _out_shape, scope='hfinal_deconv')
    elif opts['g_arch'] == 'dcgan_mod':
        last_h, _, _ = ops.deconv2d(
            opts, layer_x, _out_shape, d_h=1, d_w=1, scope='hfinal_deconv')
    if opts['input_normalize_sym']:
        return tf.nn.tanh(last_h), last_h, ld1, ld2, ld3, ld4, noo, to_monitor_dec
    else:
        return tf.nn.sigmoid(last_h), last_h

def ali_decoder(opts, noise, is_training=False, reuse=False):
    output_shape = datashapes[opts['dataset']]
    batch_size = tf.shape(noise)[0]
    noise_size = noise.get_shape()[1]
    data_height = output_shape[0]
    data_width = output_shape[1]
    data_channels = output_shape[2]
    noise = tf.reshape(noise, [-1, 1, 1, noise_size])
    num_units = opts['g_num_filters']
    layer_params = []
    layer_params.append([4, 1, num_units])
    layer_params.append([4, 2, num_units / 2])
    layer_params.append([4, 1, num_units / 4])
    layer_params.append([4, 2, num_units / 8])
    layer_params.append([5, 1, num_units / 8])
    # For convolution: (n - k) / stride + 1 = s
    # For transposed: (s - 1) * stride + k = n
    layer_x = noise
    height = 1
    width = 1
    for i, (kernel, stride, channels) in enumerate(layer_params):
        height = (height - 1) * stride + kernel
        width = height
        layer_x = ops.deconv2d(
            opts, layer_x, [batch_size, height, width, channels],
            d_h=stride, d_w=stride, scope='h%d_deconv' % i,
            conv_filters_dim=kernel, padding='VALID')
        if opts['batch_norm']:
            layer_x = ops.batch_norm(opts, layer_x, is_training,
                                     reuse, scope='h%d_bn' % i)
        layer_x = ops.lrelu(layer_x, 0.1)
    assert height == data_height
    assert width == data_width

    # Then two 1x1 convolutions.
    layer_x = ops.conv2d(opts, layer_x, num_units / 8, d_h=1, d_w=1,
                         scope='conv2d_1x1', conv_filters_dim=1)
    if opts['batch_norm']:
        layer_x = ops.batch_norm(opts, layer_x,
                                 is_training, reuse, scope='hfinal_bn')
    layer_x = ops.lrelu(layer_x, 0.1)
    layer_x = ops.conv2d(opts, layer_x, data_channels, d_h=1, d_w=1,
                         scope='conv2d_1x1_2', conv_filters_dim=1)
    if opts['input_normalize_sym']:
        return tf.nn.tanh(layer_x), layer_x
    else:
        return tf.nn.sigmoid(layer_x), layer_x

def began_decoder(opts, noise, is_training=False, reuse=False):

    output_shape = datashapes[opts['dataset']]
    num_units = opts['g_num_filters']
    num_layers = opts['g_num_layers']
    batch_size = tf.shape(noise)[0]

    h0 = ops.linear(opts, noise, num_units * 8 * 8, scope='h0_lin')
    h0 = tf.reshape(h0, [-1, 8, 8, num_units])
    layer_x = h0
    for i in range(num_layers):
        if i % 3 < 2:
            # Don't change resolution
            layer_x = ops.conv2d(opts, layer_x, num_units,
                                 d_h=1, d_w=1, scope='h%d_conv' % i)
            layer_x = tf.nn.elu(layer_x)
        else:
            if i != num_layers - 1:
                # Upsampling by factor of 2 with NN
                scale = 2 ** (i / 3 + 1)
                layer_x = ops.upsample_nn(layer_x, [scale * 8, scale * 8],
                                          scope='h%d_upsample' % i, reuse=reuse)
                # Skip connection
                append = ops.upsample_nn(h0, [scale * 8, scale * 8],
                                          scope='h%d_skipup' % i, reuse=reuse)
                layer_x = tf.concat([layer_x, append], axis=3)

    last_h = ops.conv2d(opts, layer_x, output_shape[-1],
                        d_h=1, d_w=1, scope='hfinal_conv')
    if opts['input_normalize_sym']:
        return tf.nn.tanh(last_h), last_h
    else:
        return tf.nn.sigmoid(last_h), last_h

def z_adversary(opts, inputs, reuse=False):
    num_units = opts['d_num_filters']
    num_layers = opts['d_num_layers']
    nowozin_trick = opts['gan_p_trick']
    # No convolutions as GAN happens in the latent space
    with tf.variable_scope('z_adversary', reuse=reuse):
        hi = inputs
        for i in range(num_layers):
            hi = ops.linear(opts, hi, num_units, scope='h%d_lin' % (i + 1))
            hi = tf.nn.relu(hi)
        hi = ops.linear(opts, hi, 1, scope='hfinal_lin')
        if nowozin_trick:
            # We are doing GAN between our model Qz and the true Pz.
            # Imagine we know analytical form of the true Pz.
            # The optimal discriminator for D_JS(Pz, Qz) is given by:
            # Dopt(x) = log dPz(x) - log dQz(x)
            # And we know exactly dPz(x). So add log dPz(x) explicitly 
            # to the discriminator and let it learn only the remaining
            # dQz(x) term. This appeared in the AVB paper.
            assert opts['pz'] == 'normal', \
                'The GAN Pz trick is currently available only for Gaussian Pz'
            sigma2_p = float(opts['pz_scale']) ** 2
            normsq = tf.reduce_sum(tf.square(inputs), 1)
            hi = hi - normsq / 2. / sigma2_p \
                    - 0.5 * tf.log(2. * np.pi) \
                    - 0.5 * opts['zdim'] * np.log(sigma2_p)
    return hi


def transform_noise(opts, code, eps):
    hi = code
    T = 3
    for i in range(T):
        # num_units = max(opts['zdim'] ** 2 / 2 ** (T - i), 2)
        num_units = max(2 * (i + 1) * opts['zdim'], 2)
        hi = ops.linear(opts, hi, num_units, scope='eps_h%d_lin' % (i + 1))
        hi = tf.nn.tanh(hi)
    A = ops.linear(opts, hi, opts['zdim'] ** 2, scope='eps_hfinal_lin')
    A = tf.reshape(A, [-1, opts['zdim'], opts['zdim']])
    eps = tf.reshape(eps, [-1, 1, opts['zdim']])
    res = tf.matmul(eps, A)
    res = tf.reshape(res, [-1, opts['zdim']])
    return res, A
    # return ops.linear(opts, hi, opts['zdim'] ** 2, scope='eps_hfinal_lin')
