import data
import argparse

try:
    import cPickle as pickle
except ModuleNotFoundError:
    import pickle

###############################################################################

parser = argparse.ArgumentParser(description='Generate Corpus File')

parser.add_argument(
    '--input',
    type=str,
    default="",
    help='input file')

parser.add_argument(
    '--output',
    type=str,
    default="",
    help='output file')

args = parser.parse_args()

###############################################################################


corpus = data.get_corpus(path=args.input)
with open(args.output, 'wb') as file:
    pickle.dump(corpus, file, pickle.HIGHEST_PROTOCOL)

