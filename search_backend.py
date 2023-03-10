import hashlib
import re

import nltk
import numpy as np
from gensim.models import KeyedVectors
from nltk.corpus import stopwords

from inverted_index_gcp import *

model = KeyedVectors.load_word2vec_format("/home/kathyagafonov/vector.bin", binary=True)

nltk.download('stopwords')

# Paths to run on the machine
text_path = "/home/kathyagafonov/postings_gcp_text/"
anchor_path = "/home/kathyagafonov/postings_gcp_anchor/"
title_path = "/home/kathyagafonov/postings_gcp_title/"
page_views_path = '/home/kathyagafonov/page_views_2021_08.pkl'
DL_path = "/home/kathyagafonov/doc_length_dict.pkl"
titles_dict_path = "/home/kathyagafonov/titles_dict.pkl"
page_rank = "/home/kathyagafonov/page_rank_dict.pkl"

# Reading the inverse indexes into local variables
inverted_text = InvertedIndex.read_index(text_path, "index_text")
inverted_anchor = InvertedIndex.read_index(anchor_path, "index_anchor")
inverted_title = InvertedIndex.read_index(title_path, "index_title")


def open_pkl_file(path):
    with open(path, 'rb') as f:
        return pickle.loads(f.read())


DL_dict = open_pkl_file(DL_path)
page_view_dict = open_pkl_file(page_views_path)
titles_dict = open_pkl_file(titles_dict_path)
page_rank_dict = open_pkl_file(page_rank)


# -------------------------------------------------- tokenize --------------------------------------------------
def _hash(s):
    return hashlib.blake2b(bytes(s, encoding='utf8'), digest_size=5).hexdigest()


english_stopwords = frozenset(stopwords.words('english'))
corpus_stopwords = ['category', 'references', 'also', 'links', 'extenal', 'see', 'thumb']

all_stopwords = english_stopwords.union(corpus_stopwords)
RE_WORD = re.compile(r"""[\#\@\w](['\-]?\w){2,24}""", re.UNICODE)

NUM_BUCKETS = 124


def token2bucket_id(token):
    return int(_hash(token), 16) % NUM_BUCKETS


def tokenize(text):
    list_of_tokens = [token.group() for token in RE_WORD.finditer(text.lower()) if token.group() not in all_stopwords]
    return list_of_tokens


# ---------------------------------------- tfidf function ----------------------------------------

def get_cosine_similarity(query, index, path, expand=False):
    """
    Retrieves all the document IDs that contain at least one of the terms in the query.
    The function first tokenizes the query to extract the unique terms in the query.
    Then, it iterates through each term in the query and retrieves its posting list from the index

    Args:
        expand: boolean, indicating whether to expand the query or not
        query (str): The query to search for.
        index: An object containing the index data.
        path (str): the path of the index

    Returns:
        dict: A dictionary of document IDs as keys and their TF-IDF scores as values, sorted in descending order by TF-IDF score.
    """
    doc_id_scores = {}
    tokenized_query = tokenize(query)
    if expand:
        tokenized_query = expand_query_wiki_model(tokenized_query)
    for term in set(tokenized_query):
        if term in index.df.keys():
            posting_list = index.read_posting_list(term, path)
            idf = np.log10(len(titles_dict) / index.df[term])
            for doc_id, tf in posting_list:
                tf_idf = tf * idf
                if doc_id in doc_id_scores:
                    doc_id_scores[doc_id] += tf_idf
                else:
                    doc_id_scores[doc_id] = tf_idf

    return dict(sorted(doc_id_scores.items(), key=lambda item: item[1], reverse=True))


# ---------------------------------------- binary function ----------------------------------------
def get_binary(query, index, path, expand=False):
    """
    Retrieves all the document IDs that contain at least one of the terms in the query.
    The function first tokenizes the query to extract the unique terms in the query.
    Then, it iterates through each term in the query and retrieves its posting list from the index

    Args:
        expand: boolean, indicating whether to expand the query or nor
        query (str): The query to search for.
        index: An object containing the index data.
        path (str): the path of the index

    Returns:
        dict: A dictionary of document IDs as keys and their word counts as values,
        sorted in descending order by word count.
    """
    doc_id_word_counts = {}
    tokenized_query = tokenize(query)
    if expand:
        tokenized_query = expand_query_wiki_model(tokenized_query)
    for term in set(tokenized_query):
        if term in index.df.keys():
            posting_list = index.read_posting_list(term, path)
            for doc_id, word_count in posting_list:
                if doc_id in doc_id_word_counts:
                    doc_id_word_counts[doc_id] += 1
                else:
                    doc_id_word_counts[doc_id] = 1

    return dict(sorted(doc_id_word_counts.items(), key=lambda item: item[1], reverse=True))


# ------------------------------- The sub-functions of the search function -------------------------------

def combined_search(query, w1=0.97, w2=0.03):
    """
    Args:
        query: query to search not tokenized
        w1: weight for page_views
        w2: weight for page_rank

    Returns:
        dict: A dictionary of document IDs as keys and their word counts as values,
        sorted in descending order by word count.
    """
    results = dict()
    if len(tokenize(query)) <= 2:
        results = get_binary(query, inverted_title, title_path, True)
        text_scores = get_cosine_similarity(query, inverted_text, text_path, True)
        results.update(text_scores)
    else:
        combined_score = {}
        text_scores = get_cosine_similarity(query, inverted_text, text_path, True)
        title_scores = get_binary(query, inverted_title, title_path, True)
        anchor_scores = get_binary(query, inverted_anchor, anchor_path, True)
        merged = [key for key in text_scores if key in title_scores and key in anchor_scores]

        if merged:
            for doc_id in merged:
                combined_score[doc_id] = (w1 * page_view_dict.get(doc_id, 0)) + (w2 * page_rank_dict.get(doc_id, 0))
            results = dict(sorted(combined_score.items(), key=lambda item: item[1], reverse=True))
    return results


# -----------------------------------  model expend -----------------------------------

def expand_query_wiki_model(query_tokens):
    '''
    Expands the query
    Args:
        query_tokens: token of a given query
    Returns: a new list of expanded tokens
    '''
    new_tokens = []
    for tok in query_tokens:
        if tok in model:
            sim = model.most_similar(tok, topn=8)
            for word, similarity in sim:
                if similarity > 0.5:
                    new_tokens.append(word[0])
        new_tokens.append(tok)
    return new_tokens


# -----------------------------------  get statistics for page rank/ page view -----------------------------------
def get_page_stats(wiki_ids, statistics_dict):
    """
    This function takes in a list of wiki_ids and a statistics_dict.
    It returns a list of the statistics of pages corresponding to the passed wiki_ids
    from the statistics_dict. If a wiki_id is not present in the statistics_dict, it returns 0 for that id.

    Parameters:
        - wiki_ids (list): A list of wiki ids
        - statistics_dict (dict): A dictionary containing the statistics of pages.

    Returns:
        - list: A list of statistics of pages corresponding to the passed wiki_ids.
    """
    result = []
    for doc_id in wiki_ids:
        result.append(statistics_dict.get(doc_id, 0))
    return result
