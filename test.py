#python
from fs import *

fs = open_fs('test.fs')
w = FSWalker(fs)
for e in w.get_entries():
  w.remove(e)
