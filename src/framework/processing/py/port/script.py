import logging
import json
import io

import pandas as pd

import port.api.props as props
import port.validate as validate
import port.google_home as google_home

from port.api.commands import (CommandSystemDonate, CommandUIRender, CommandSystemExit)

LOG_STREAM = io.StringIO()

logging.basicConfig(
    stream=LOG_STREAM,
    level=logging.DEBUG,
    format="%(asctime)s --- %(name)s --- %(levelname)s --- %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

LOGGER = logging.getLogger("script")


def process(session_id):
    LOGGER.info("Starting the donation flow")
    yield donate_logs(f"{session_id}-tracking")

    platforms = [
        ("Google Home", extract_google_home, google_home.validate),
    ]

    # For each platform
    # 1. Prompt file extraction loop
    # 2. In case of succes render data on screen
    for platform in platforms:
        platform_name, extraction_fun, validation_fun = platform

        table_list = None

        # Prompt file extraction loop
        while True:
            LOGGER.info("Prompt for file for %s", platform_name)
            yield donate_logs(f"{session_id}-tracking")

            # Render the propmt file page
            promptFile = prompt_file("application/zip, text/plain, application/json")
            file_result = yield render_donation_page(platform_name, promptFile)

            if file_result.__type__ == "PayloadString":
                validation = validation_fun(file_result.value)

                # DDP is recognized: Status code zero
                if validation.status_code.id == 0: 
                    LOGGER.info("Payload for %s", platform_name)
                    yield donate_logs(f"{session_id}-tracking")

                    table_list = extraction_fun(file_result.value, validation)
                    break

                # DDP is not recognized: Different status code
                if validation.status_code.id != 0: 
                    LOGGER.info("Not a valid %s zip; No payload; prompt retry_confirmation", platform_name)
                    yield donate_logs(f"{session_id}-tracking")
                    retry_result = yield render_donation_page(platform_name, retry_confirmation(platform_name))

                    if retry_result.__type__ == "PayloadTrue":
                        continue
                    else:
                        LOGGER.info("Skipped during retry %s", platform_name)
                        yield donate_logs(f"{session_id}-tracking")
                        break
            else:
                LOGGER.info("Skipped %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")
                break

        # Render data on screen
        if table_list is not None:
            LOGGER.info("Prompt consent; %s", platform_name)
            yield donate_logs(f"{session_id}-tracking")

            # Check if extract something got extracted
            if len(table_list) == 0:
                table_list.append(create_empty_table(platform_name))

            prompt = assemble_tables_into_form(table_list)
            consent_result = yield render_donation_page(platform_name, prompt)

            if consent_result.__type__ == "PayloadJSON":
                LOGGER.info("Data donated; %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")
                yield donate(platform_name, consent_result.value)

                questionnaire_results = yield render_questionnaire()
                if questionnaire_results.__type__ == "PayloadJSON":
                    yield donate(f"{session_id}-{platform_name}-questionnaire-donation", questionnaire_results.value)
                else:
                    LOGGER.info("Skipped questionnaire: %s", platform_name)
                    yield donate_logs(f"{session_id}-{platform_name}-tracking")

            # Data was not donated
            else:
                LOGGER.info("Skipped ater reviewing consent: %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")

                # render sad questionnaire
                render_questionnaire_results = yield render_questionnaire_no_donation()
                if render_questionnaire_results.__type__ == "PayloadJSON":
                    yield donate(f"{session_id}--{platform_name}-questionnaire-no-donation", render_questionnaire_results.value)
                else:
                    LOGGER.info("Skipped questionnaire no donation: %s", platform_name)
                    yield donate_logs(f"{session_id}-tracking")

    yield render_end_page()
    yield exit(0, "Success")



##################################################################

def assemble_tables_into_form(table_list: list[props.PropsUIPromptConsentFormTable]) -> props.PropsUIPromptConsentForm:
    """
    Assembles all donated data in consent form to be displayed
    """
    return props.PropsUIPromptConsentForm(table_list, [])


def donate_logs(key):
    log_string = LOG_STREAM.getvalue()  # read the log stream
    if log_string:
        log_data = log_string.split("\n")
    else:
        log_data = ["no logs"]

    return donate(key, json.dumps(log_data))


def create_empty_table(platform_name: str) -> props.PropsUIPromptConsentFormTable:
    """
    Show something in case no data was extracted
    """
    title = props.Translatable({
       "en": "Er ging niks mis, maar we konden niks vinden",
       "nl": "Er ging niks mis, maar we konden niks vinden"
    })
    df = pd.DataFrame(["No data found"], columns=["No data found"])
    table = props.PropsUIPromptConsentFormTable(f"{platform_name}_no_data_found", title, df)
    return table


##################################################################
# Extraction functions

def extract_google_home(zipfile: str, validation: validate.ValidateInput) -> list[props.PropsUIPromptConsentFormTable]:
    """
    Main data extraction function. Assemble all extraction logic here.
    """
    tables_to_render = []

    df = google_home.google_home_to_df(zipfile, validation)
    if not df.empty:

        wordcloud = {
            "title": {"en": "", "nl": ""},
            "type": "wordcloud",
            "textColumn": "Uw commando"
        }
        table_title = props.Translatable({"en": "Your Google Assistant Data", "nl": "Uw Google Assistant Data"})
        table_description = props.Translatable({
            "en": "CHANGE THIS In de table ziet u uw data, in het figuur hieronder ziet u een wordcloud. Hier kun je een heel verhaaltje typen. Druk op het vergrootglas om een grotere woordwolk te krijgen", 
            "nl": "CHANGE THIS In de table ziet u uw data, in het figuur hieronder ziet u een wordcloud. Druk op het vergrootglas om een grotere woordwolk te krijgen", 
        })
        table =  props.PropsUIPromptConsentFormTable("google_home_unique_key_here", table_title, df, table_description, [wordcloud])
        tables_to_render.append(table)

    return tables_to_render



##########################################
# Functions provided by Eyra did not change

def render_end_page():
    page = props.PropsUIPageEnd()
    return CommandUIRender(page)


def render_donation_page(platform, body):
    header = props.PropsUIHeader(
        props.Translatable(
            {"en": "Uw Google Home gegevens delen", 
             "nl": "Uw Google Home gegevens delen"}
        ))
    footer = props.PropsUIFooter()
    page = props.PropsUIPageDonation(platform, header, body, footer)
    return CommandUIRender(page)


def retry_confirmation(platform):
    text = props.Translatable(
        {
            "en": f"Unfortunately, we could not process your {platform} file. If you are sure that you selected the correct file, press Continue. To select a different file, press Try again.",
            "nl": f"Helaas, kunnen we uw {platform} bestand niet verwerken. Weet u zeker dat u het juiste bestand heeft gekozen? Ga dan verder. Probeer opnieuw als u een ander bestand wilt kiezen."
        }
    )
    ok = props.Translatable({"en": "Try again", "nl": "Probeer opnieuw"})
    cancel = props.Translatable({"en": "Continue", "nl": "Verder"})
    return props.PropsUIPromptConfirm(text, ok, cancel)


def prompt_file(extensions):
    description = props.Translatable(
        {
            "en": f"Please follow the download instructions and choose the file that you stored on your device.",
            "nl": f"Volg de download instructies en kies het bestand dat u opgeslagen heeft op uw apparaat. "
        }
    )
    return props.PropsUIPromptFileInput(description, extensions)


def donate(key, json_string):
    return CommandSystemDonate(key, json_string)


def exit(code, info):
    return CommandSystemExit(code, info)



###############################################################################################
# Questionnaire questions

#Not donate questions
NO_DONATION_REASONS = props.Translatable({
    "en": "What is/are the reason(s) that you decided not to share your data?",
    "nl": "Wat is de reden dat u er voor gekozen hebt uw data niet te delen?"
})


def render_questionnaire():
    platform_name = "Google Home"

    understanding = props.Translatable({
        "en": "How would you describe the information you shared with the researchers at the University of Amsterdam?",
        "nl": "Hoe zou u de informatie omschrijven die u heeft gedeeld met de onderzoekers van de Universiteit van Amsterdam?"
    })

    indentify_consumption = props.Translatable({"en": f"If you have viewed the information, to what extent do you recognize your own interactions with Google Home?",
                                                "nl": f"Als u de informatie heeft bekeken, in hoeverre herkent u dan uw eigen interacties met Google Home?"})
    identify_consumption_choices = [
        props.Translatable({"en": f"I recognized my own interactions on {platform_name}",
                            "nl": f"Ik herkende mijn interacties met {platform_name}"}),
        props.Translatable({"en": f"I recognized my {platform_name} interactions and of those I share my account with",
                            "nl": f"Ik herkende mijn interacties met {platform_name} en die van anderen met wie ik mijn account deel"}),
        props.Translatable({"en": f"I recognized mostly the interactions of those I share my account with",
                            "nl": f"Ik herkende vooral de interacties van anderen met wie ik mijn account deel"}),
        props.Translatable({"en": f"I did not look at my data ",
                            "nl": f"Ik heb niet naar mijn gegevens gekeken"}),
        props.Translatable({"en": f"Other",
                            "nl": f"Anders"})
    ]

    enjoyment = props.Translatable({"en": "In case you looked at the data presented on this page, how interesting did you find looking at your data?", "nl": "Als u naar uw data hebt gekeken, hoe interessant vond u het om daar naar te kijken?"})
    enjoyment_choices = [
        props.Translatable({"en": "not at all interesting", "nl": "Helemaal niet interessant"}),
        props.Translatable({"en": "somewhat uninteresting", "nl": "Een beetje oninteressant"}),
        props.Translatable({"en": "neither interesting nor uninteresting", "nl": "Niet interessant, niet oninteressant"}),
        props.Translatable({"en": "somewhat interesting", "nl": "Een beetje interessant"}),
        props.Translatable({"en": "very interesting", "nl": "Erg interessant"})
    ]

    awareness = props.Translatable({"en": f"Did you know that {platform_name} collected this data about you?",
                                    "nl": f"Wist u dat {platform_name} deze gegevens over u verzamelde?"})
    awareness_choices = [
        props.Translatable({"en":"Yes", "nl": "Ja"}),
        props.Translatable({"en":"No", "nl": "Nee"})
    ]

    additional_comments = props.Translatable({
        "en": "Do you have any additional comments about the donation? Please add them here.",
        "nl": "Heeft u nog andere opmerkingen? Laat die hier achter."
    })

    questions = [
        props.PropsUIQuestionOpen(question=understanding, id=1),
        props.PropsUIQuestionMultipleChoice(question=indentify_consumption, id=2, choices=identify_consumption_choices),
        props.PropsUIQuestionMultipleChoice(question=enjoyment, id=3, choices=enjoyment_choices),
        props.PropsUIQuestionMultipleChoice(question=awareness, id=4, choices=awareness_choices),
        props.PropsUIQuestionOpen(question=additional_comments, id=5),
    ]

    description = props.Translatable({"en": "Below you can find a couple of questions about the data donation process", "nl": "Hieronder vind u een paar vragen over het data donatie process"})
    header = props.PropsUIHeader(props.Translatable({"en": "Questionnaire", "nl": "Vragenlijst"}))
    body = props.PropsUIPromptQuestionnaire(questions=questions, description=description)
    footer = props.PropsUIFooter()

    page = props.PropsUIPageDonation("page", header, body, footer)
    return CommandUIRender(page)




def render_questionnaire_no_donation():
    platform_name = "Google Home"

    understanding = props.Translatable({
        "en": "How would you describe the information you shared with the researchers at the University of Amsterdam?",
        "nl": "Hoe zou u de informatie omschrijven die u heeft gedeeld met de onderzoekers van de Universiteit van Amsterdam?"
    })

    indentify_consumption = props.Translatable({"en": f"If you have viewed the information, to what extent do you recognize your own interactions with Google Home?",
                                                "nl": f"Als u de informatie heeft bekeken, in hoeverre herkent u dan uw eigen interacties met Google Home?"})
    identify_consumption_choices = [
        props.Translatable({"en": f"I recognized my own interactions on {platform_name}",
                            "nl": f"Ik herkende mijn interacties met {platform_name}"}),
        props.Translatable({"en": f"I recognized my {platform_name} interactions and of those I share my account with",
                            "nl": f"Ik herkende mijn interacties met {platform_name} en die van anderen met wie ik mijn account deel"}),
        props.Translatable({"en": f"I recognized mostly the interactions of those I share my account with",
                            "nl": f"Ik herkende vooral de interacties van anderen met wie ik mijn account deel"}),
        props.Translatable({"en": f"I did not look at my data ",
                            "nl": f"Ik heb niet naar mijn gegevens gekeken"}),
        props.Translatable({"en": f"Other",
                            "nl": f"Anders"})
    ]

    enjoyment = props.Translatable({"en": "In case you looked at the data presented on this page, how interesting did you find looking at your data?", "nl": "Als u naar uw data hebt gekeken, hoe interessant vond u het om daar naar te kijken?"})
    enjoyment_choices = [
        props.Translatable({"en": "not at all interesting", "nl": "Helemaal niet interessant"}),
        props.Translatable({"en": "somewhat uninteresting", "nl": "Een beetje oninteressant"}),
        props.Translatable({"en": "neither interesting nor uninteresting", "nl": "Niet interessant, niet oninteressant"}),
        props.Translatable({"en": "somewhat interesting", "nl": "Een beetje interessant"}),
        props.Translatable({"en": "very interesting", "nl": "Erg interessant"})
    ]

    awareness = props.Translatable({"en": f"Did you know that {platform_name} collected this data about you?",
                                    "nl": f"Wist u dat {platform_name} deze gegevens over u verzamelde?"})
    awareness_choices = [
        props.Translatable({"en":"Yes", "nl": "Ja"}),
        props.Translatable({"en":"No", "nl": "Nee"})
    ]

    additional_comments = props.Translatable({
        "en": "Do you have any additional comments about the donation? Please add them here.",
        "nl": "Heeft u nog andere opmerkingen? Laat die hier achter."
    })

    questions = [
        props.PropsUIQuestionOpen(question=understanding, id=1),
        props.PropsUIQuestionMultipleChoice(question=indentify_consumption, id=2, choices=identify_consumption_choices),
        props.PropsUIQuestionMultipleChoice(question=enjoyment, id=3, choices=enjoyment_choices),
        props.PropsUIQuestionMultipleChoice(question=awareness, id=4, choices=awareness_choices),
        props.PropsUIQuestionOpen(question=NO_DONATION_REASONS, id=6),
        props.PropsUIQuestionOpen(question=additional_comments, id=5),
    ]

    description = props.Translatable({"en": "Below you can find a couple of questions about the data donation process", "nl": "Hieronder vind u een paar vragen over het data donatie process"})
    header = props.PropsUIHeader(props.Translatable({"en": "Questionnaire", "nl": "Vragenlijst"}))
    body = props.PropsUIPromptQuestionnaire(questions=questions, description=description)
    footer = props.PropsUIFooter()

    page = props.PropsUIPageDonation("page", header, body, footer)
    return CommandUIRender(page)

