import fs, argparse

def main(path, block_size, num_blocks):
  try:
    f = fs.create_fs(path, block_size, num_blocks)
    print f, 'created'
  except IOError as e:
    print str(e)

if __name__ == '__main__':
  p = argparse.ArgumentParser(description='create a filesystem')
  p.add_argument('path', help='path at which to create the filesystem')
  p.add_argument('--block-size', '-bs', type=int, help='block size', default=fs.DEFAULT_BLOCK_SIZE)
  p.add_argument('--num-blocks', '-nb', type=int, help='number of blocks')
  import sys
  ns = p.parse_args(sys.argv[1:])
  main(**vars(ns))
