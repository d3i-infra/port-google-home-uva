"""
DDP extract Google Home
"""
from pathlib import Path
import logging
import zipfile

import zipfile
import json
from lxml import etree

import pandas as pd

from port.validate import (
    DDPCategory,
    Language,
    DDPFiletype,
    ValidateInput,
    StatusCode,
)
import port.helpers as helpers
import port.unzipddp as unzipddp

logger = logging.getLogger(__name__)


DDP_CATEGORIES = [
    DDPCategory(
        id="html_nl",
        ddp_filetype=DDPFiletype.HTML,
        language=Language.NL,
        known_files=[
            "archive_browser.html",
            "MyActivity.html",
        ],
    ),
    DDPCategory(
        id="htlm_en",
        ddp_filetype=DDPFiletype.HTML,
        language=Language.EN,
        known_files=[
            "archive_browser.html",
            "My Activity.html",
        ],
    ),
    DDPCategory(
        id="html_de",
        ddp_filetype=DDPFiletype.HTML,
        language=Language.DE,
        known_files=[
            "Archiv_Übersicht.html",
            "MeineAktivitäten.html",
        ],
    ),
    DDPCategory(
        id="json_de",
        ddp_filetype=DDPFiletype.JSON,
        language=Language.DE,
        known_files=[
            "Archiv_Übersicht.html",
            "MeineAktivitäten.json",
        ],
    ),
    DDPCategory(
        id="json_nl",
        ddp_filetype=DDPFiletype.JSON,
        language=Language.NL,
        known_files=[
            "archive_browser.html",
            "MyActivity.json",
        ],
    ),
]


STATUS_CODES = [
    StatusCode(id=0, description="Valid DDP", message=""),
    StatusCode(id=1, description="Valid DDP unhandled format", message=""),
    StatusCode(id=2, description="Bad zipfile", message=""),
]


def validate(zfile: Path) -> ValidateInput:
    """
    Validates the input of an GoogleHome zipfile
    """
    validation = ValidateInput(STATUS_CODES, DDP_CATEGORIES)

    try:
        paths = []
        with zipfile.ZipFile(zfile, "r") as zf:
            for f in zf.namelist():
                p = Path(f)
                if p.suffix in (".json", ".csv", ".html"):
                    logger.debug("Found: %s in zip", p.name)
                    paths.append(p.name)

        if validation.infer_ddp_category(paths):
                validation.set_status_code(0)
        else:
            validation.set_status_code(1)

    except zipfile.BadZipFile:
        validation.set_status_code(2)

    return validation



def json_data_to_dataframe(json_data) -> pd.DataFrame:
    out = pd.DataFrame()
    try:
        # Check if the loaded data is a list
        if isinstance(json_data, list):
            # Create a DataFrame from the list of objects
            out = pd.DataFrame(json_data)

        else:
            print("The JSON data is not a list.")

    except json.JSONDecodeError as e:
        print(f"JSON decoding error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        return out
 

def is_nan(value):
    if isinstance(value, float):
        return value != value  # NaN is the only value that is not equal to itself
    return False


def clean_response(response_list: list) -> str:
    """
    Get the list from a response
    Extract all values from the name key

    """
    responses = []
    try:
        if isinstance(response_list, list):
            for d in response_list:
                responses.append(d.get("name", ""))

            out = " ".join(responses)
           
        if is_nan(response_list):
            out = "Geen reactie"

        return out

    except Exception as e:
        return str(response_list)
        
        
def clean_extracted_data(df: pd.DataFrame) -> pd.DataFrame:
    out = df

    try:
        # Extract relevant columns
        selected_columns = ['title', 'time', 'subtitles']
        df_cleaned = df.loc[:, selected_columns]

        # Create 'command' and 'response' columns
        df_cleaned['Uw commando'] = df_cleaned['title'].astype(str)
        df_cleaned['Reactie van de assistent'] = df_cleaned['subtitles'].apply(clean_response)

        # Remove additional columns
        columns_to_remove2 = ['title', 'subtitles']
        df_to_donate = df_cleaned.drop(columns=columns_to_remove2, axis=1)

        # Remove last word in entries of the Commando column (ger: gesagt, en: said, nl: gezegd)
        df_to_donate['Uw commando'] = df_to_donate['Uw commando'].str.rsplit(' ', 1).str[0]
        # For NL this means also removing 'Je hebt' in combination with 'gezegd'
        df_to_donate['Uw commando'] = df_to_donate['Uw commando'].str.replace('Je hebt', '')


        # Dropping miliseconds and adjusting format of day and time
        df_to_donate['Dag en tijd'] = df_to_donate['time'].str.replace(r"\.\d+", "")
        # Replace 'T' with ',' and remove 'Z'
        df_to_donate['Dag en tijd'] = df_to_donate['Dag en tijd'].str.replace('T', ', ').str.replace('Z', '')

        # Select and reorder columns
        out = df_to_donate[['Dag en tijd', 'Uw commando', 'Reactie van de assistent']]
    except Exception as e:
        print(e)
    finally:
        return out
    


def google_home_html_to_df(html_buf):
    """
    Should work with the HTML of all languages
    """

    datapoints = []
    try:
        html = html_buf.read()
        tree = etree.HTML(html)
        card_class = "content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1"
        r = tree.xpath(f"//div[@class='{card_class}']")

        for n in r:

            date = ""
            command = ""
            response = "Geen reactie"
            card_node = n.xpath("node()")

            for i, element in enumerate(card_node):
                if hasattr(element, 'tag'):
                    if element.tag == "a":
                        command = helpers.fix_latin1_string(element.text)

                    if element.tag == "br" and i < len(card_node) - 2:
                        response = helpers.fix_latin1_string(card_node[i + 1])

            date = card_node.pop()
            datapoints.append(
                (date, command, response)
            )
    except Exception as e:
        logger.error(e)

    out = pd.DataFrame(datapoints, columns=["Dag en tijd", "Uw commando", "Reactie van de assistent"])
    return out



def google_home_to_df(google_home_zip: str, validation: ValidateInput) -> pd.DataFrame:

    out = pd.DataFrame()

    # CODE FOR HTML 
    if validation.ddp_category.ddp_filetype == DDPFiletype.HTML:
        file_name = "MyActivity.html" # this is the dutch file name

        if validation.ddp_category.language == Language.DE:
            file_name = "MeineAktivitäten.html"

        if validation.ddp_category.language == Language.EN:
            file_name = "My Activity.html"

        buf = unzipddp.extract_file_from_zip(google_home_zip, file_name)
        out = google_home_html_to_df(buf)


    # CODE FOR JSON NOT TESTED YET
    if validation.ddp_category.ddp_filetype == DDPFiletype.JSON:
        file_name = "MyActivity.json" # this is the dutch file name

        if validation.ddp_category.language == Language.DE:
            file_name = "MeineAktivitäten.json"

        buf = unzipddp.extract_file_from_zip(google_home_zip, file_name)
        json = unzipddp.read_json_from_bytes(buf)
        df = json_data_to_dataframe(json)
        out = clean_extracted_data(df)

    return out

