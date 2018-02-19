# coding : utf-8

"""
Learning procedure of residual blocks in ResFGB.
"""

from __future__ import print_function, absolute_import, division, unicode_literals
import sys
import logging
import numpy as np
import theano
import theano.tensor as T
from utils import minibatches, minibatch_indices
import models.layers as L
from models.mlp_block import MLPBlock

try:
    from numba import jit, f4

    @jit( f4[:,:]( f4[:,:], f4[:,:] ), nopython=True )
    def __dot__( a, b ):
        return np.dot( a, b )

except ImportError:
    def __dot__( a, b ):
        return np.dot( a, b )

class ResGrad( object ):
    def __init__( self, model, eta, resblock_hparams={},
                  seed=99, proc_batch_size=10000 ):

        self.show_param( eta, 
                         resblock_hparams['tune_eta'],
                         resblock_hparams['max_epoch'], 
                         resblock_hparams['early_stop'],
                         seed )

        self.params         = []
        self.__eta__        = eta
        self.__tune_eta__   = resblock_hparams['tune_eta'] 
        self.__max_epoch__  = resblock_hparams['max_epoch']
        self.__early_stop__ = resblock_hparams['early_stop']
        del resblock_hparams['tune_eta']
        del resblock_hparams['max_epoch']
        del resblock_hparams['early_stop']

        """
        Large-data will be divided and processed by the batch_size just for the computational efficiency.
        If you encounter the segmentation fault, let you reduce the batch_size.
        However, note that it may slow down the computation time for small/middle-data sets.
        """
        self.__batch_size__ = proc_batch_size
        
        # compile 
        self.__regressor__  = MLPBlock( seed=seed, **resblock_hparams )
        self.__model__      = model
        self.__zgrad__      = T.grad( cost=self.__model__.loss, wrt=self.__model__.Z )
        self.__zgrad_func__ = theano.function( [ self.__model__.Z, self.__model__.Y ], 
                                               self.__zgrad__ )

    def show_param( self, eta, tune_eta, max_epoch, early_stop, seed ):
        logging.info( '{0:<5}{1:^26}{2:>5}'.format( '-'*5, 'ResGrad setting', '-'*5 ) )
        logging.info( '{0:<15}{1:>21.7f}'.format( 'fg_eta', eta ) )
        logging.info( '{0:<15}{1:>21}'.format( 'tune_eta', tune_eta ) )
        logging.info( '{0:<15}{1:>21}'.format( 'max_epoch', max_epoch ) )
        logging.info( '{0:<15}{1:>21}'.format( 'early_stop', early_stop ) )
        logging.info( '{0:<15}{1:>21}'.format( 'seed', seed ) )

    def predict( self, X ):
        return self.apply( X )

    def solve_approximation( self, Z, zgrads, n_iter ):
        self.__regressor__.optimizer.reset_func()

        eps = 1e-10
        znorm = np.sqrt( np.mean( zgrads ** 2, axis=1 ) ) + eps           
        logging.log( logging.DEBUG, 'min: {0:12.5f}, max: {1:12.5f}'\
                     .format( np.min(znorm), np.max(znorm) ) )

        if self.__tune_eta__ and (n_iter==0):
            self.__regressor__.determine_eta( Z, zgrads / znorm[:,None] )

        self.__regressor__.fit( Z, zgrads / znorm[:,None], self.__max_epoch__,
                                early_stop=self.__early_stop__,
                                level=logging.INFO )

    def compute_weight( self, Z, Y, n_iter ):
        """
        Compute the weight matrix for functional gradient method.

        Arguments
        ---------
        Z : Numpy array. 
            Represents data.
        Y : Numpy array.
            Represents label.
        """
        n, d = Z.shape

        fullbatch_mode = True if n <= self.__batch_size__ or self.__batch_size__ is None else False

        if fullbatch_mode:
            # (n, d)
            zgrads = self.__zgrad_func__(Z,Y)

            self.solve_approximation( Z, zgrads, n_iter )
            Zl = self.__regressor__.predict(Z) 

            # Zl: (n,emb_dim), zgrads: (n,d)
            # Wl: (emb_dim,d)
            Wl = __dot__( Zl.T, zgrads )
        else:
            Wl = np.zeros( shape=(d,d), dtype=theano.config.floatX )
            zgrads = []
            for i, start in enumerate( range(0,n,self.__batch_size__) ):
                end = min( n, start+self.__batch_size__ )
                Zb  = Z[start:end]
                Yb  = Y[start:end]
                b   = Zb.shape[0]
                zgrads.append( self.__zgrad_func__(Zb,Yb) * float(b) / float(n) )

            zgrads = np.vstack( zgrads )

            self.solve_approximation( Z, zgrads, n_iter )

            for i, start in enumerate( range(0,n,self.__batch_size__) ):
                end = min( n, start+self.__batch_size__ )
                Zb = Z[start:end]
                Yb = Y[start:end]
                zgradb = zgrads[start:end]
                Zlb = self.__regressor__.predict(Zb)

                # (batch,d)
                b = Zb.shape[0]

                # Zlb: (n,emb_dim), zgradb: (n,d)
                # Wl: (emb_dim,d)
                Wl += __dot__( Zlb.T, zgradb )

        self.params.append( Wl )
        self.params[-1] *= self.__eta__
        
    def apply( self, Z_, lfrom=0 ):
        """
         Perform functional gradient descent.
        """

        if len(self.params)==0:
            return Z_

        n = Z_.shape[0]
        fullbatch_mode = True if n <= self.__batch_size__ else False
        shape = Z_.shape

        if fullbatch_mode:
            Z = np.array(Z_)

            for i, Wl in enumerate( self.params[lfrom:] ):
                l = lfrom + i
                Wl = Wl

                Zk = self.__regressor__.predict(Z) 
                Tk = __dot__( Zk, Wl )
                Z -= Tk
            
            return Z.asnumpy()
        else:
            Z = []
            for start in range(0,n,self.__batch_size__):
                end = min( n, start+self.__batch_size__ )
                Zb = np.array( Z_[start:end] )
                shape = Zb.shape

                for i, Wl in enumerate( self.params[lfrom:] ):
                    l = lfrom + i
                        
                    Zkb = self.__regressor__.predict(Zb)

                    Tkb = __dot__( Zkb, Wl )
                    Zb -= Tkb

                Z.append( Zb )
            return np.vstack(Z)