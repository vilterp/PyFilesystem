import struct
import re
from os import SEEK_SET, SEEK_CUR

DEFAULT_BLOCK_SIZE = 128
HEADER_SIZE = 1 + 1 + 4 + 4
NUM_POINTERS = 12
INODE_HEADER_SIZE = 1 + 4 + NUM_POINTERS * 4
VERSION = (1, 0)
VALID_NAME_RE = re.compile(r'^[^\t\n\r\f\v/]+$')

# FIXME: currently can't have spaces in filenames (but make sure they're not all spaces!)
# FIXME: VALID_NAME_RE doesn't exclude ".."
# TODO: walker should work with path names with slashes
# TODO: FSWalker#move (with paths)
# TODO: FS10#open

def create_fs(path, block_size=DEFAULT_BLOCK_SIZE, num_blocks=None, fs_version=VERSION):
  if not num_blocks:
    num_blocks = block_size
  # create (doesn't create in r+b mode)
  h = open(path, 'w')
  h.close()
  # open for real
  h = open(path, 'r+b', 0) # unbuffered
  # write fs information block (block 0)
  # major version (1 byte) | minor version (1) | block_size (4 bytes) | num_blocks (4 bytes) | empty |
  h.write(chr(fs_version[0]))
  h.write(chr(fs_version[1]))
  h.write(struct.pack('ii', block_size, num_blocks))
  for i in xrange(block_size - HEADER_SIZE):
    h.write('\x00')
  # write block allocation bitmap (block 1)
  bools = [True, True]
  bools.extend([False for i in xrange(8-2)])
  h.write(bools_to_char(bools))
  all_false = bools_to_char([False for i in xrange(8)])
  for byte in xrange(block_size - 1):
    h.write(all_false)
  # write empty blocks
  for block in xrange(num_blocks - 2):
    for byte in xrange(block_size):
      h.write('\x00')
  # new fs object
  fs = FS10(h, block_size, num_blocks)
  # write inode for root directory
  root_block_ind = fs.alloc_block()
  blocks = [fs.alloc_block()]
  blocks.extend([0 for i in range(NUM_POINTERS - 1)])
  fs.write_inode(Inode(root_block_ind, '', True, 0, blocks))
  # return the fs
  return fs

def open_fs(path):
  h = open(path, 'r+b', 0)
  version = (ord(h.read(1)), ord(h.read(1)))
  block_size, num_blocks = struct.unpack('ii', h.read(8))
  h.read(block_size - HEADER_SIZE)
  return FS10(h, block_size, num_blocks)

class FS10:
  
  def __init__(self, handle, block_size, num_blocks):
    self.handle = handle
    self.block_size = block_size
    self.num_blocks = num_blocks
    self.MAX_FILE_LENGTH = NUM_POINTERS * block_size
    self.CAPACITY = block_size * (num_blocks - 2) # doesn't include inodes
    self.MAX_DIR_ENTRIES = self.MAX_FILE_LENGTH / 4
    self.MAX_NAME_LENGTH = self.block_size - INODE_HEADER_SIZE
  
  def __repr__(self):
    return "<FS10 from '%s' block_size=%d num_blocks=%d>" % (self.handle.name, self.block_size, self.num_blocks)
  
  def seek_to_block(self, block_ind):
    self.handle.seek(block_ind * self.block_size, SEEK_SET)
  
  def alloc_block(self):
    self.seek_to_block(1)
    for byte_ind in xrange(self.block_size):
      bools = char_to_bools(self.handle.read(1))
      for b in xrange(len(bools)):
        block_ind = byte_ind * 8 + b
        val = bools[b]
        if not val:
          # mark full
          bools[b] = True
          self.handle.seek(-1, SEEK_CUR)
          self.handle.write(bools_to_char(bools))
          return block_ind
    raise FSFull()
  
  def free_block(self, block_ind):
    self.seek_to_block(1)
    self.handle.seek(block_ind / 8, SEEK_CUR)
    bools = char_to_bools(self.handle.read(1))
    self.handle.seek(-1, SEEK_CUR)
    bools[block_ind % 8] = False
    self.handle.write(bools_to_char(bools))
  
  def read_inode(self, block_ind):
    # Inode disk layout:
    # | is_dir (1 byte) | length (4) | pointers (4 * 12 = 48 bytes) | name (rest; null-terminated) |
    self.seek_to_block(block_ind)
    is_dir = struct.unpack('?', self.handle.read(1))[0]
    length = struct.unpack('i', self.handle.read(4))[0]
    blocks = []
    for i in xrange(NUM_POINTERS):
      blocks.append(struct.unpack('i', self.handle.read(4))[0])
    name = ''
    for i in xrange(self.block_size - INODE_HEADER_SIZE):
      b = self.handle.read(1)
      if b == '\x00':
        break
      else:
        name += b
    return Inode(block_ind, name, is_dir, length, blocks)
  
  def write_inode(self, inode):
    self.seek_to_block(inode.block_ind)
    self.handle.write(struct.pack('?', inode.is_dir))
    self.handle.write(struct.pack('i', inode.length))
    assert len(inode.blocks) == NUM_POINTERS, 'len(inode.blocks) must be 12'
    for b in inode.blocks:
      self.handle.write(struct.pack('i', b))
    assert len(inode.name) <= self.MAX_NAME_LENGTH, 'name %s is too long' % inode.name
    self.handle.write(inode.name)
    for i in xrange(self.MAX_NAME_LENGTH - len(inode.name)):
      self.handle.write('\x00')
  

class Inode:
  
  def __init__(self, block_ind, name, is_dir, length, blocks=None):
    self.block_ind = block_ind
    self.name = name
    self.is_dir = is_dir
    self.length = length
    assert len(blocks) == NUM_POINTERS
    self.blocks = blocks
  
  def __repr__(self):
    return "<Inode %d '%s' (%s) len=%d blocks=%s>" % (self.block_ind, self.name,
                                                      'dir' if self.is_dir else 'file',
                                                      self.length, str(self.blocks))
  

class FSWalker:
  
  def __init__(self, fs):
    self.fs = fs
    self.stack = []
    # anchor self at root inode
    root_inode = fs.read_inode(2)
    root_handle = DirHandle(fs, root_inode)
    self.stack.append(root_handle)
  
  def __repr__(self):
    return "<FSWalker of '%s' at '%s'>" % (self.fs.handle.name, self.cur_path())
  
  def at_root(self):
    return len(self.stack) == 1
  
  def cur_path(self):
    if self.at_root():
      return '/'
    else:
      return '/'.join([d.name for d in self.stack])
  
  def exists(self, name):
    return self.cur_dir().exists(name)
  
  def get_entries(self):
    return self.cur_dir().get_entries()
  
  def cur_dir(self):
    return self.stack[-1]
  
  def enter_dir(self, dirname):
    entries = self.cur_dir().get_entries()
    try:
      new_dir = entries[dirname]
      if new_dir.is_dir():
        self.stack.append(new_dir)
      else:
        raise NotADir(dirname)
    except KeyError:
      raise DoesNotExist(dirname)
  
  def cd_up(self):
    if self.at_root():
      raise Exception("can't cd up; already at root")
    else:
      self.stack.pop()
  
  def create_dir(self, name):
    return self.cur_dir().create_dir(name)
  
  def create_file(self, name):
    return self.cur_dir().create_file(name)
  
  def remove(self, name):
    self.cur_dir().remove(name)
  
  def remove_dir_recursive(self, name):
    try:
      self.enter_dir(name)
      entries = [e for e in self.get_entries().itervalues()]
      for entry in entries:
        if entry.is_dir():
          self.remove_dir_recursive(entry.name)
        else:
          self.remove(entry.name)
      self.cd_up()
      self.remove(name)
    except KeyError:
      raise DoesNotExist(name)
  

class Handle:
  
  def __init__(self, fs, inode):
    self.name = inode.name
    self.fs = fs
    self.inode = inode
    self.cursor = 0
    self.real_cursor = [0, 0] # (block ind, byte ind within block)
  
  def length(self):
    return self.inode.length
  
  def seek_abs(self, new_ind):
    if new_ind >= 0 and new_ind <= self.length():
      self.cursor = new_ind
      block_size = self.fs.block_size
      self.real_cursor[0] = self.cursor / block_size
      self.real_cursor[1] = self.cursor % block_size
    else:
      raise SeekOutOfBounds('seeked to %d, file length %d' % (new_ind, self.length()))
  
  def seek_rel(self, amt):
    self.seek_abs(self.cursor + amt)
  
  def seek_from_end(self, amt):
    self.seek_abs(self.length() - amt)
  
  def seek_to_beg(self):
    self.seek_abs(0)
  
  def seek_to_end(self):
    self.seek_abs(self.length())
  
  def at_end(self):
    return self.cursor == self.length()
  
  def read_one(self):
    if self.at_end():
      raise ReadOutOfBounds()
    if self.at_block_border():
      self.real_cursor[0] += 1
      self.real_cursor[1] = 0
      self.seek_to_real_cursor()
    c = self.fs.handle.read(1)
    self.cursor += 1
    self.real_cursor[1] += 1
    return c
  
  def read(self, amt=None):
    self.seek_to_real_cursor()
    buf = ''
    if amt is None:
      while not self.at_end():
        buf += self.read_one()
    else:
      for i in xrange(amt):
        buf += self.read_one()
    return buf
  
  def read_int(self):
    return struct.unpack('i', self.read(4))[0]
  
  def write_int(self, val):
    self.write(struct.pack('i', val))
  
  def at_block_border(self):
    return self.real_cursor[1] == self.fs.block_size
  
  def add_block_and_seek(self):
    next_pointer_ind = self.real_cursor[0] + 1
    new_block_ind = self.fs.alloc_block()
    self.inode.blocks[next_pointer_ind] = new_block_ind
    self.real_cursor[0] += 1
    self.real_cursor[1] = 0
    self.fs.seek_to_block(new_block_ind)
  
  def seek_to_real_cursor(self):
    block_ind = self.inode.blocks[self.real_cursor[0]]
    ind = block_ind * self.fs.block_size + self.real_cursor[1]
    self.fs.handle.seek(ind, SEEK_SET)
  
  def write(self, data):
    self.seek_to_real_cursor()
    inode_dirty = False
    for c in data:
      if self.at_end(): # we're appending
        appending = True
        self.inode.length += 1
        inode_dirty = True
        if self.length() > self.fs.MAX_FILE_LENGTH:
          raise FileFull()
      else:
        appending = False
      if self.at_block_border():
        if appending:
          self.add_block_and_seek()
        else:
          self.real_cursor[0] += 1
          self.real_cursor[1] = 0
      self.fs.handle.write(c)
      self.cursor += 1
      self.real_cursor[1] += 1
    if inode_dirty:
      self.fs.write_inode(self.inode)
  
  def shrink(self, amt):
    if amt > self.length():
      raise ShrinkOutOfBounds(self.length, amt)
    self.inode.length -= amt
    # move cursor if necessary
    if self.cursor > self.length():
      self.seek_to_end() # updates cursor & real_cursor
    if amt > self.real_cursor[1]:
      # shrinking more than just within current block (don't have to do anything otherwise)
      shrink_in_first_block = amt - self.real_cursor[1]
      shrink_in_last_block = (amt - shrink_in_first_block) % self.fs.block_size
      num_blocks_to_free = (amt - shrink_in_first_block - shrink_in_last_block) / self.fs.block_size
      pointer_ind = self.cursor / self.fs.block_size
      # free rest of blocks
      if num_blocks_to_free >= 1:
        for i in xrange(num_blocks_to_free + 1):
          self.fs.free_block(self.inode.blocks[pointer_ind])
          self.inode.blocks[pointer_ind] = 0
          pointer_ind -= 1
    self.fs.write_inode(self.inode)
  
  def clear(self):
    self.shrink(self.length())
  

class FileHandle(Handle):
  
  def __repr__(self):
    return "<FileHandle '%s' length=%d cursor=%d>" % (self.name, self.length(), self.cursor)
  
  def is_dir(self):
    return False
  

class DirHandle(Handle):
  
  def __repr__(self):
    return "<DirHandle '%s' entries=%d>" % (self.name, self.num_entries())
  
  def num_entries(self):
    return self.length() / 4
  
  def is_empty(self):
    return self.num_entries() == 0
  
  def get_pointers(self):
    pointers = []
    self.seek_to_beg()
    while not self.at_end():
      pointers.append(self.read_int())
    return pointers
  
  def get_entries(self):
    try:
      return self.entries
    except AttributeError:
      entries = {}
      for ptr in self.get_pointers():
        inode = self.fs.read_inode(ptr)
        if inode.is_dir:
          entry = DirHandle(self.fs, inode)
        else:
          entry = FileHandle(self.fs, inode)
        entries[inode.name] = entry
      self.entries = entries
      return entries
  
  def exists(self, entry_name):
    return entry_name in self.get_entries()
  
  def is_dir(self):
    return True
  
  def create_child_inode(self, name, is_dir):
    if not is_valid_name(name):
      raise InvalidName(name)
    if name in self.get_entries():
      raise AlreadyExists(name)
    inode_ind = self.fs.alloc_block()
    first_block = self.fs.alloc_block()
    blocks = [first_block]
    blocks.extend([0 for i in xrange(NUM_POINTERS - 1)])
    inode = Inode(inode_ind, name, is_dir, 0, blocks)
    self.fs.write_inode(inode)
    self.seek_to_end()
    self.write_int(inode_ind)
    return inode
  
  def create_dir(self, name):
    inode = self.create_child_inode(name, True)
    handle = DirHandle(self.fs, inode)
    self.entries[name] = handle
    return handle
  
  def create_file(self, name):
    inode = self.create_child_inode(name, False)
    handle = FileHandle(self.fs, inode)
    self.entries[name] = handle
    return handle
  
  def remove(self, name):
    try:
      handle = self.get_entries()[name]
    except KeyError:
      raise DoesNotExist(name)
    if handle.is_dir():
      if not handle.is_empty():
        raise DirNotEmpty()
    inode = handle.inode
    # remove inode pointer from dir's contents
    pointers = self.get_pointers()
    ptr_ind = pointers.index(inode.block_ind)
    if ptr_ind == len(pointers)-1: # last pointer
      self.shrink(4)
    else:
      self.seek_from_end(4)
      last_ptr = self.read_int()
      self.seek_abs(ptr_ind * 4)
      self.write_int(last_ptr)
      self.shrink(4)
    # free the entry's blocks
    for block_pointer in inode.blocks:
      if block_pointer == 0:
        break
      self.fs.free_block(block_pointer)
    self.fs.free_block(inode.block_ind)
    del self.entries[name]
  
  def rename(self, name, newname):
    if self.exists(name):
      if not self.exists(newname):
        h = self.get_entries()[name]
        inode = h.inode
        inode.name = newname
        self.fs.write_inode(inode)
      else:
        raise AlreadyExists()      
    else:
      raise DoesNotExist()
  

def is_valid_name(name):
  return VALID_NAME_RE.match(name) is not None

class FSException(Exception):
  pass

class ShrinkOutOfBounds(FSException):
  
  def __init__(self, length, amt):
    self.length = length
    self.amt = amt
  
  def __str__(self):
    return 'ShrinkOutOfBounds: shrank by %d; file length was only %d' % (self.amt, self.length)
  

class NotADir(FSException):
  pass

class NotAFile(FSException):
  pass

class DirNotEmpty(FSException):
  pass

class AlreadyExists(FSException):
  pass

class DoesNotExist(FSException):
  pass

class InvalidName(FSException):
  pass

class SeekOutOfBounds(FSException):
  pass

class ReadOutOfBounds(FSException):
  pass

class FileFull(FSException):
  pass

class FSFull(FSException):
  pass

def bools_to_char(bools):
  assert len(bools) == 8, 'must pass in 8 booleans'
  x = 0
  for i in xrange(len(bools)):
    if bools[i]:
      x += 2 ** i
  return chr(x)

def char_to_bools(char):
  x = ord(char)
  bools = []
  for i in reversed(xrange(8)):
    bools.append(x % 2 == 1)
    x /= 2
  return bools

# from http://blogmag.net/blog/read/38/Print_human_readable_file_size
def humansize(num):
  for x in ['bytes','KB','MB','GB','TB']:
    if num < 1024.0:
      return "%3.1f%s" % (num, x)
    num /= 1024.0
