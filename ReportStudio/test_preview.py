import unittest
import model
import numpy as np
from wave_preview import envelope

class PreviewTests(unittest.TestCase):
    def test_peak_survives(self):
        t=np.arange(10000,dtype=float);y=np.zeros(10000);y[3456]=99
        ts,ys=envelope(t,y,0,9999,100)
        self.assertEqual(ys.max(),99)
        self.assertTrue(np.all(np.diff(ts)>0))
    def test_small_not_decimated(self):
        t=np.arange(5);y=t*2
        ts,ys=envelope(t,y,0,4)
        np.testing.assert_equal(ts,t);np.testing.assert_equal(ys,y)

if __name__=='__main__':unittest.main()
