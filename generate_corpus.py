import data

try:
    import cPickle as pickle
except ModuleNotFoundError:
    import pickle


corpus = data.get_corpus(path="data/wsj_sample")
with open("data/wsj_corpus", 'wb') as file:
    pickle.dump(corpus, file, pickle.HIGHEST_PROTOCOL)

