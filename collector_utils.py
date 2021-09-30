import requests
import itertools
import re
import urllib.parse
import time
import sys
from SPARQLWrapper import SPARQLWrapper, JSON


"""
Retrieve a unique list of values in GBIF occurrence dwc.recordedBy fields for a given date range and taxon  

:param start_year: start year of query date range (YYYY)
:param end_year: end year of query date range (YYYY)
:param taxon_key: GBIF taxon key
:return: Set of names (unique strings)
"""


def get_gbif_recordedBy(start_year, end_year, taxon_key):
    # Get the value of recordedBy for each record, where it exists
    collectors = set()

    for offset in itertools.count(step=300):
        r = requests.get(f"""https://api.gbif.org/v1/occurrence/search?year={start_year},{end_year}
        &basisOfRecord=PRESERVED_SPECIMEN&taxon_key={taxon_key}&limit=300&offset={offset}""").json()

        # print(f"offset: {offset}")

        # Get the info we're interested in
        # Could expand get recordedBy ID, inst/dataset ID, taxa of interest, years of activity?
        for d in r['results']:
            if 'recordedBy' in d:
                recordedBy = d['recordedBy']
                # These delimiters seem to usually indicate > 1 name, so split and add back in
                collectors.update(split_multiple_names(recordedBy, '&|\|| and |;'))
            else:
                continue

        if r['endOfRecords']:
            break

    return collectors


"""
Utility func to turn a single gbif species id into something human-readable
"""


def get_species_label(gbif_species_id):
    response = requests.get(f"https://api.gbif.org/v1/species/{gbif_species_id}")
    json_response = response.json()
    name_label = json_response['scientificName']

    return name_label


"""
Identify strings which are more likely to contain a full given name vs. those that probably don't. 
Filters out single-word strings, leading or trailing single-character initials, unless the string also includes
a title in (Mrs, Miss)

:param input_names: iterable of names
:return: List of full names, list of thinner/harder-to-resolve names
"""


def get_rid_of_gunk(input_names):
    good_names = []
    initials = []

    for p_name in input_names:  # there's probably a better way of combining all these regex eh sarah
        front_match = re.search('^[a-zA-Z]{3}', p_name)
        tail_match = re.search('[a-zA-Z]{3}$', p_name)
        multi_words = re.search('\s', p_name)

        # Only keep if there's a miss/mrs title, or if it doesn't start or end with an initial
        if (front_match and tail_match and multi_words) or 'Mrs' in p_name or 'Miss' in p_name:
            good_names.append(p_name)
        else:
            initials.append(p_name)

    return good_names, initials


"""
Break up strings that include common list delimiter characters 

:param input_names: iterable of names that might be a stringified list
:param delimiters: Pipe delimited, single-string list of split-on characters. e.g., '&|\|| and |;'
:return: Input list with additional items resulting from split appended
"""


def split_multiple_names(input_names, delimiters):
    return [s.strip() for s in re.split(delimiters, input_names)]


"""
Broad-brush check for matches against Bionomia

"""


def search_bionomia_people_auto(names, cutoff_score=50):
    autocomplete_base_url = 'https://api.bionomia.net/user.json?q='  # Useful to get confidence scores of match

    matches = []
    unmatches = []

    # url encode each name string and get result + score from autocomplete_base_url
    for name in names:

        # print(f"{name}...")

        response = requests.get(f"{autocomplete_base_url}{urllib.parse.quote_plus(name)}&limit=1")
        response.raise_for_status()

        # Un-matching queries return an empty list
        if len(response.text) == 2:
            unmatches.append(name)
        else:
            # stash top result dict with original query/name string
            json_response = response.json()
            top_match = json_response[0]
            if top_match['score'] < cutoff_score:
                unmatches.append(name)
            else:
                matches.append({'original_name': name, 'bionomia_match': json_response[0]})

    return matches, unmatches


"""
Stricter check for matches against Bionomia - won't return matches if the collection data is not within the 
lifespan of the person defined in wikidata/bionomia (ex. year of birth)

"""


def search_bionomia_people_detail(names, year):
    details_base_url = 'https://api.bionomia.net/users/search?'

    for name in names:

        # throttle the connection - getting connection timeout errors so maybe this will help...
        time.sleep(1)

        query_params = {'q': name['original_name'],
                        'date': year,
                        'strict': 'true',
                        'limit': 1
                        }
        response = requests.get(details_base_url, params=query_params)
        response.raise_for_status()

        # print(response.url)

        json_response = response.json()

        # Check we've returned results - no score/confidence cutoff availabel here though
        result_count = json_response['opensearch:totalResults']

        # unpack the first ['item'] in dataElement and handle no results scenario
        if result_count == 0:
            continue
        else:
            # store in the original dict to help compare results from the different approachs
            name['bionomia_detail_match'] = json_response['dataFeedElement'][0]['item']

    return names


def get_wikidata_botanists(endpoint_url, query, wikidata_username):
    user_agent = f"WDQS-example Python/{sys.version_info[0]}.{sys.version_info[1]} ({wikidata_username})"

    sparql = SPARQLWrapper(endpoint_url, agent=user_agent)
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    return sparql.query().convert()


# Extract QIDs
def get_qid(botanist):
    botanist_qid = botanist['botanist']['value'].rsplit('/',1)[1]
    return botanist_qid