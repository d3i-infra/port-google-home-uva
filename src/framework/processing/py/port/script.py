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
                        yield donate_status(f"{session_id}-SKIPPED-RETRY", "STARTED")
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
                yield donate_status(f"{session_id}-DONATED", "DONATED")

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
                    yield donate_status(f"{session_id}-{platform_name}-SKIP-REVIEW-CONSENT", "SKIP_REVIEW_CONSENT")
                    yield donate_logs(f"{session_id}-tracking")

    yield exit(0, "Success")
    yield render_end_page()



##################################################################

def assemble_tables_into_form(table_list: list[props.PropsUIPromptConsentFormTable]) -> props.PropsUIPromptConsentForm:
    """
    Assembles all donated data in consent form to be displayed
    """

    description = props.Translatable({
       "en": "Below you will find data about your own Google Assistant usage. Please review the data carefully and remove any information you do not wish to share. If you would like to share these data, click on the 'Yes, share for research' button at the bottom of this page. By sharing these data, you contribute to research on how families use smart speakers.",
       "nl": "Hieronder ziet u gegevens over uw eigen Google Assistent gebruik. Bekijk de gegevens zorgvuldig, en verwijder de gegevens die u niet wilt delen. Als u deze gegevens wilt delen, klik dan op de knop 'Ja, deel voor onderzoek' onderaan deze pagina. Door deze gegevens te delen draagt u bij aan onderzoek over hoe gezinnen smart speakers gebruiken."
    })

    donate_question = props.Translatable({
       "en": "Do you want to share these data for research?",
       "nl": "Wilt u deze gegevens delen voor onderzoek?"
    })

    donate_button = props.Translatable({
       "en": "Yes, share for research",
       "nl": "Ja, deel voor onderzoek"
    })

    return props.PropsUIPromptConsentForm(
       table_list, 
       [], 
       description = description,
       donate_question = donate_question,
       donate_button = donate_button
    )


def donate_logs(key):
    log_string = LOG_STREAM.getvalue()  # read the log stream
    if log_string:
        log_data = log_string.split("\n")
    else:
        log_data = ["no logs"]

    return donate(key, json.dumps(log_data))


def donate_status(filename: str, message: str):
    return donate(filename, json.dumps({"status": message}))


def create_empty_table(platform_name: str) -> props.PropsUIPromptConsentFormTable:
    """
    Show something in case no data was extracted
    """
    title = props.Translatable({
       "en": "Nothing went wrong, but we could not find anything",
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
        table_title = props.Translatable({"en": "Your Google Assistant Data", "nl": "Uw Google Assistent gegevens"})
        table_description = props.Translatable({
            "en": "You can see at what day and time what command was understood by the assistant and what the device might have said or done in response. You have the option to select specific rows in the table and remove them if you do not want to share them with us. Below the table you see a word cloud of the most frequent words in your commands. The bigger the word the more often it was used. You can click on the magnifying glass to make the word cloud bigger.", 
            "nl": "U kunt zien op welke dag en tijd welk commando werd begrepen door de assistent en wat het apparaat mogelijk heeft gezegd of gedaan als reactie. U hebt de optie om specifieke rijen in de tabel te selecteren en te verwijderen als u ze niet met ons wilt delen. Onder de tabel ziet u een woordwolk van de meest voorkomende woorden in uw commando's. Hoe grooter het woord, hoe vaaker het werd gebruikt. U kunt op het vergrootglas klikken om de woordenwolk groter te maken.", 
        })
        table =  props.PropsUIPromptConsentFormTable("google_home_data", table_title, df, table_description, [wordcloud])
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
            {"en": "Sharing your Google Assistant data", 
             "nl": "Uw Google Assistent gegevens delen"}
        ))
    footer = props.PropsUIFooter()
    page = props.PropsUIPageDonation(platform, header, body, footer)
    return CommandUIRender(page)


def retry_confirmation(platform):
    text = props.Translatable(
        {
            "en": f"Unfortunately, we could not process your {platform} file. If you are sure that you selected the correct file, press Continue. To select a different file, press 'Try again'.",
            "nl": f"Helaas, kunnen we uw {platform} bestand niet verwerken. Weet u zeker dat u het juiste bestand heeft gekozen? Ga dan verder. Klik op 'Probeer opnieuw' als u een ander bestand wilt kiezen."
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
    platform_name = "Google"

    #understanding = props.Translatable({
    #    "en": "How would you describe the information you shared with the researchers at the University of Amsterdam?",
    #    "nl": "Hoe zou u de informatie omschrijven die u heeft gedeeld met de onderzoekers van de Universiteit van Amsterdam?"
    #})

    indentify_consumption = props.Translatable({"en": f"If you have viewed the information, to what extent do you recognize your own interactions with Google Assistant?",
                                                "nl": f"Als u de informatie heeft bekeken, in hoeverre herkent u dan uw eigen interacties met Google Assistent?"})
    identify_consumption_choices = [
        props.Translatable({"en": f"I recognized my own interactions with Google Assistant",
                            "nl": f"Ik herkende mijn interacties met Google Assistent"}),
        props.Translatable({"en": f"I recognized the interactions with Google Assistant of myself and of those I share my account with",
                            "nl": f"Ik herkende mijn interacties met Google Assistent en die van anderen met wie ik mijn account deel"}),
        props.Translatable({"en": f"I recognized mostly the interactions with Google Assistant of those I share my account with",
                            "nl": f"Ik herkende vooral de interacties met Google Assistent van anderen met wie ik mijn account deel"}),
        props.Translatable({"en": f"I did not look at my data ",
                            "nl": f"Ik heb niet naar mijn gegevens gekeken"}),
        props.Translatable({"en": f"Other",
                            "nl": f"Anders"})
    ]

    #enjoyment = props.Translatable({"en": "In case you looked at the data presented on this page, how interesting did you find looking at your data?", "nl": "Als u naar uw data hebt gekeken, hoe interessant vond u het om daar naar te kijken?"})
    #enjoyment_choices = [
    #    props.Translatable({"en": "not at all interesting", "nl": "Helemaal niet interessant"}),
    #    props.Translatable({"en": "somewhat uninteresting", "nl": "Een beetje oninteressant"}),
    #    props.Translatable({"en": "neither interesting nor uninteresting", "nl": "Niet interessant, niet oninteressant"}),
    #    props.Translatable({"en": "somewhat interesting", "nl": "Een beetje interessant"}),
    #    props.Translatable({"en": "very interesting", "nl": "Erg interessant"})
    #]

    awareness = props.Translatable({"en": f"Did you know that {platform_name} collected these data about you?",
                                    "nl": f"Wist u dat {platform_name} deze gegevens over u verzamelde?"})
    awareness_choices = [
        props.Translatable({"en":"Yes", "nl": "Ja"}),
        props.Translatable({"en":"No", "nl": "Nee"})
    ]

    additional_comments = props.Translatable({
        "en": "You can now delete the file that you obtained from Google and stored in the download-folder of your device. If you have any additional comments about this part of the study, please add them here.",
        "nl": "U kunt nu het bestand verwijderen dat u van Google heeft gekregen en dat is opgeslagen in de downloadmap van uw computer/laptop. Als u nog aanvullende opmerkingen heeft over dit deel van het onderzoek, laat ze hier achter."
    })

    questions = [
        #props.PropsUIQuestionOpen(question=understanding, id=1),
        props.PropsUIQuestionMultipleChoice(question=indentify_consumption, id=2, choices=identify_consumption_choices),
        #props.PropsUIQuestionMultipleChoice(question=enjoyment, id=3, choices=enjoyment_choices),
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
    platform_name = "Google"

    #understanding = props.Translatable({
    #    "en": "How would you describe the information you shared with the researchers at the University of Amsterdam?",
    #    "nl": "Hoe zou u de informatie omschrijven die u heeft gedeeld met de onderzoekers van de Universiteit van Amsterdam?"
    #})

    #indentify_consumption = props.Translatable({"en": f"If you have viewed the information, to what extent do you recognize your own interactions with Google Home?",
    #                                            "nl": f"Als u de informatie heeft bekeken, in hoeverre herkent u dan uw eigen interacties met Google Home?"})
    #identify_consumption_choices = [
    #    props.Translatable({"en": f"I recognized my own interactions on {platform_name}",
    #                        "nl": f"Ik herkende mijn interacties met {platform_name}"}),
    #    props.Translatable({"en": f"I recognized my {platform_name} interactions and of those I share my account with",
    #                        "nl": f"Ik herkende mijn interacties met {platform_name} en die van anderen met wie ik mijn account deel"}),
    #    props.Translatable({"en": f"I recognized mostly the interactions of those I share my account with",
    #                        "nl": f"Ik herkende vooral de interacties van anderen met wie ik mijn account deel"}),
    #    props.Translatable({"en": f"I did not look at my data ",
    #                        "nl": f"Ik heb niet naar mijn gegevens gekeken"}),
    #    props.Translatable({"en": f"Other",
    #                        "nl": f"Anders"})
    #]

    #enjoyment = props.Translatable({"en": "In case you looked at the data presented on this page, how interesting did you find looking at your data?", "nl": "Als u naar uw data hebt gekeken, hoe interessant vond u het om daar naar te kijken?"})
    #enjoyment_choices = [
    #    props.Translatable({"en": "not at all interesting", "nl": "Helemaal niet interessant"}),
    #    props.Translatable({"en": "somewhat uninteresting", "nl": "Een beetje oninteressant"}),
    #    props.Translatable({"en": "neither interesting nor uninteresting", "nl": "Niet interessant, niet oninteressant"}),
    #    props.Translatable({"en": "somewhat interesting", "nl": "Een beetje interessant"}),
    #    props.Translatable({"en": "very interesting", "nl": "Erg interessant"})
    #]

    awareness = props.Translatable({"en": f"Did you know that {platform_name} collected these data about you?",
                                    "nl": f"Wist u dat {platform_name} deze gegevens over u verzamelde?"})
    awareness_choices = [
        props.Translatable({"en":"Yes", "nl": "Ja"}),
        props.Translatable({"en":"No", "nl": "Nee"})
    ]

    additional_comments = props.Translatable({
        "en": "You can now delete the file that you obtained from Google and stored in the download-folder of your device. If you have any additional comments about this part of the study, please add them here",
        "nl": "U kunt nu het bestand verwijderen dat u van Google heeft gekregen en dat is opgeslagen in de downloadmap van uw computer/laptop. Als u nog aanvullende opmerkingen heeft over dit deel van het onderzoek, laat ze hier achter."
    })

    questions = [
        #props.PropsUIQuestionOpen(question=understanding, id=1),
        #props.PropsUIQuestionMultipleChoice(question=indentify_consumption, id=2, choices=identify_consumption_choices),
        #props.PropsUIQuestionMultipleChoice(question=enjoyment, id=3, choices=enjoyment_choices),
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

