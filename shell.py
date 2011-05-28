from fs import *
import sys, traceback, shlex

def cmd(func):
  func.isCmd = True
  return func

class ExecError(Exception):
  """errors that happen because the command cannot be executed:
     either doesn't exist, or wrong args
  """
  def __init__(self, msg):
    self.msg = msg
  

class InternalError(Exception):
  """errors which happen inside the command.
     these are bugs, and cause the shell to crash.
  """
  def __init__(self, exc_info):
    self.exc_info = exc_info
  

class UserError(Exception):
  """errors which happen in the command.
     Not bugs, just require printing so the user knows what's wrong
  """
  def __init__(self, msg):
    self.msg = msg
  

def listsplit(l, delim):
  parts = []
  buf = []
  for item in l:
    if item == delim:
      parts.append(buf)
      buf = []
    else:
      buf.append(item)
  parts.append(buf)
  return parts

class Shell:
  
  def __init__(self, fs):
    self.fs = fs
    self.walker = FSWalker(self.fs)
    self.cmds = []
    for attr in dir(self):
      member = getattr(self, attr)
      if hasattr(member, 'isCmd'):
        self.cmds.append(attr)
  
  def run(self):
    while True:
      line = raw_input('%s@%s$ ' % (self.fs.handle.name, self.walker.cur_path()))
      if line is None:
        continue
      tokens = shlex.split(line)
      cmds = listsplit(tokens, '|')
      try:
        pipe = None
        for cmd in cmds:
          cmd_name = cmd[0]
          args = cmd[1:]
          pipe = self.eval_cmd(cmd_name, pipe, args)
        if pipe is not None and len(pipe) > 0:
          print pipe
      except ExecError as e:
        print e.msg
      except UserError as e:
        print e.msg
      except FSException as e:
        # TODO: human-readable error messages
        print str(e)
      except InternalError as e:
        traceback.print_exception(*e.exc_info)
        sys.exit(1)
  
  def eval_cmd(self, cmd, stdin, args):
    if cmd in self.cmds:
      try:
        return getattr(self, cmd)(stdin, *args)
      except TypeError as e:
        if str(e).startswith(cmd):
          raise ExecError(str(e))
        else:
          raise InternalError(sys.exc_info())
    else:
      raise ExecError("no such command: '%s'" % cmd)
  
  @cmd
  def help(self, stdin):
    ans = 'commands:\n\n'
    ans += '\n'.join(self.cmds)
    return ans
  
  @cmd
  def shrink(self, stdin, name, amt):
    try:
      h = self.walker.get_entries()[name]
      h.shrink(int(amt))
    except KeyError:
      raise UserError("no such entry: '%s'" % name)
    except ShrinkOutOfBounds as e:
      raise UserError(str(e))
    except ValueError:
      raise UserError('usage: shrink [name] [amount:integer]')
  
  @cmd
  def cd(self, stdin, dirname):
    if dirname == '..':
      self.walker.cd_up()
    else:
      self.walker.enter_dir(dirname)
  
  @cmd
  def ls(self, stdin):
    entries = self.walker.get_entries()
    names = [e for e in entries]
    names.sort()
    def infostr(h):
      if h.is_dir():
        return '[%d ent.]' % h.num_entries()
      else:
        return '(%s)' % humansize(h.length())
    return '\n'.join(['%s %s' % (name, infostr(entries[name])) for name in names])
  
  @cmd
  def inode(self, stdin, name=None):
    try:
      if name is None:
        handle = self.walker.cur_dir()
      else:
        handle = self.walker.get_entries()[name]
      inode = handle.inode
      return str(inode)
    except KeyError:
      raise UserError("No such entry: '%s'" % name)
  
  @cmd
  def pointers(self, stdin):
    return str(self.walker.cur_dir().get_pointers())
  
  @cmd
  def fsstats(self, stdin):
    ans = ''
    ans += 'max file length: %s\n' % humansize(self.fs.MAX_FILE_LENGTH)
    ans += 'max dir entries: %d\n' % self.fs.MAX_DIR_ENTRIES
    ans += 'max name length: %d\n' % self.fs.MAX_NAME_LENGTH
    ans += 'capacity: %s' % humansize(self.fs.CAPACITY)
    return ans
  
  @cmd
  def echo(self, stdin, *args):
    return ' '.join(args)
  
  @cmd
  def read(self, stdin, filename):
    try:
      h = self.walker.get_entries()[filename]
      if h.is_dir():
        raise UserError("'%s' is a directory" % filename)
      h.seek_to_beg()
      ans = h.read()
      h.seek_to_beg()
      return ans
    except KeyError:
      raise UserError('no such file: %s' % filename)
  
  @cmd
  def write(self, stdin, filename, newcontents=None):
    def do_write(filename, data):
      if self.walker.exists(filename):
        h = self.get_entries()[filename]
        if h.is_dir():
          raise UserError("'%s' is a directory" % filename)
        else:
          h.clear()
          h.write(data)
          h.seek_to_beg()
      else:
        h = self.walker.create_file(filename)
        h.write(data)
        h.seek_to_beg()
    
    if newcontents is None:
      if stdin is None:
        raise UserError('either pass input to stdin or as an argument')
      else:
        do_write(filename, stdin)
    else:
      do_write(filename, newcontents)
  
  @cmd
  def readext(self, stdin, name):
    try:
      return open(name).read()
    except IOError as e:
      raise UserError(str(e))
  
  @cmd
  def writeext(self, stdin, name):
    if stdin is None:
      raise UserError('usage: <input> | writeext <file>')
    else:
      try:
        f = open(name, 'w')
        f.write(stdin)
        f.close()
      except IOError as e:
        raise UserError(str(e))
  
  @cmd
  def mkdir(self, stdin, name):
    if self.walker.exists(name):
      raise UserError("entry '%s' already exists" % name)
    else:
      self.walker.cur_dir().create_dir(name)
  
  @cmd
  def touch(self, stdin, name):
    if self.walker.exists(name):
      raise UserError("entry '%s' already exists" % name)
    else:
      self.walker.cur_dir().create_file(name)
  
  @cmd
  def rm(self, stdin, name):
    try:
      self.walker.cur_dir().remove(name)
    except DoesNotExist:
      raise UserError("no such entry: '%s'" % name)
    except DirNotEmpty:
      raise UserError("directory not empty: '%s' (try using rmr)" % name)
  
  @cmd
  def rn(self, stdin, name, newname):
    try:
      self.walker.cur_dir().rename(name, newname)
    except DoesNotExist:
      raise UserError("no such entry: '%s'" % name)
    except AlreadyExists:
      raise UserError("already exists: '%s'" % newname)
  
  @cmd
  def rmr(self, stdin, name):
    try:
      self.walker.remove_dir_recursive(name)
    except NotADir:
      raise UserError("'%s' is not a directory" % name)
    except DoesNotExist:
      raise UserError("no such directory: '%s'" % name)
  
  @cmd
  def tree(self, stdin):
    def t(ans, depth):
      indent = '  ' * depth
      for entry in self.walker.get_entries().itervalues():
        if entry.is_dir():
          ans.append('%s%s/' % (indent, entry.name))
          self.walker.enter_dir(entry.name)
          t(ans, depth + 1)
          self.walker.cd_up()
        else:
          ans.append('%s%s' % (indent, entry.name))
      return ans
    
    return '\n'.join(t([], 0))
  

def main():
  if len(sys.argv) != 2:
    print 'usage: python shell.py [fs_path]'
  path = sys.argv[1]
  try:
    fs = open_fs(path)
    shell = Shell(fs)
    shell.run()
  except IOError as e:
    print str(e)
  except KeyboardInterrupt:
    print
  except EOFError:
    print

if __name__ == '__main__':
  main()
