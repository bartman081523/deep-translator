import requests
from typing import List, Optional, Union
from deep_translator.base import BaseTranslator
from deep_translator.constants import BASE_URLS
from deep_translator.exceptions import (
    RequestError,
    TooManyRequests,
    TranslationNotFound,
    ReversoTranslateError,
    ServerException
)
from deep_translator.validate import is_empty, is_input_valid, request_failed
import time
import logging

# Configure logging
#logging.basicConfig(level=logging.INFO,
#                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ReversoTranslator(BaseTranslator):
    """
    A class to interact with the Reverso translation API.
    """

    def __init__(self,
                 source: str = "auto",
                 target: str = "en",
                 proxies: Optional[dict] = None,
                 **kwargs,
                 ):
        """
        :param source: source language to translate from
        :param target: target language to translate to
        """

        self._base_url = BASE_URLS.get("REVERSO")

        # Convert language names to Reverso codes for the payload
        self.reverso_langs = {
            'arabic': 'ar', 'german': 'de', 'english': 'en', 'spanish': 'es',
            'french': 'fr', 'hebrew': 'he', 'italian': 'it', 'japanese': 'ja',
            'dutch': 'nl', 'polish': 'pl', 'portuguese': 'pt', 'romanian': 'ro',
            'russian': 'ru', 'turkish': 'tr', 'chinese': 'zh'
        }

        self._language_from = self.reverso_langs.get(source, source)
        self._language_to = self.reverso_langs.get(target, target)

        super().__init__(
            base_url=self._base_url,
            source=source,
            target=target,
            languages=self.reverso_langs,
            payload_key=None,  # Payload key is now handled within the class
        )

        # use a requests session to maintain the same headers across requests
        self._session = requests.Session()
        self._session.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json',
            'Host': 'api.reverso.net',
            'Origin': 'https://www.reverso.net',
            'Referer': 'https://www.reverso.net/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'TE': 'trailers',
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/118.0',
            'X-Reverso-Origin': 'translation.web'
        })
        self._payload = {
            "format": "text",
            "from": self._language_from,
            "to": self._language_to,
            "input": "",
            "options": {
                "languageDetection": False,
                "sentenceSplitter": True,
                "origin": "translation.web",
                "contextResults": True
            }
        }

    def translate(self, text: str, return_all: bool = False, **kwargs) -> Union[str, List[str]]:
        """
        Translate the given input text using Reverso's API.

        Args:
            text (str): The text to translate.
            return_all (bool): Flag to return all translations.
            **kwargs: Additional keyword arguments.

        Returns:
            Union[str, List[str]]: The translated text or list of translations.

        Raises:
            ReversoTranslateError: If an error occurs during translation.
            TranslationNotFound: If no translation is found for the given text.
        """
        if not is_input_valid(text, max_chars=2000):
            raise ValueError("Invalid input text. It should be a non-empty string.")

        if self._same_source_target() or is_empty(text):
            return text

        self._payload["input"] = text
        self._payload["from"] = self._language_from
        self._payload["to"] = self._language_to

        # Adjust language detection in payload options based on source language
        self._payload["options"]["languageDetection"] = self._source == "auto"

        max_retries = 3  # Maximale Anzahl an Wiederholungen
        retry_delay = 1  # Wartezeit in Sekunden zwischen den Wiederholungen

        for attempt in range(max_retries):
            try:
                logger.debug(f"Sending request to {self._base_url} with payload: {self._payload}")
                response = self._session.post(url=self._base_url, json=self._payload, timeout=30)
                logger.debug(f"Response status code: {response.status_code}")
                logger.debug(f"Response headers: {response.headers}")
                logger.debug(f"Response body: {response.text}")

                if response.status_code == 429:
                    raise TooManyRequests()

                if request_failed(status_code=response.status_code):
                    raise RequestError(response.status_code)

                data = response.json()
                if 'translation' in data:
                    translations = data['translation']
                    if return_all:
                        return translations
                    else:
                        return translations[0]
                else:
                    raise TranslationNotFound(text)

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff: Wartezeit bei jedem Versuch verdoppeln
                else:
                    logger.error("Max retries reached. Giving up.")
                    raise  # Nach dem letzten Versuch den Fehler weitergeben

            except ReversoTranslateError as e:
                logger.error(f"Translation error: {e}")
                raise

            except TranslationNotFound as e:
                logger.error(f"Translation not found: {e}")
                raise

            except Exception as e:
                logger.error(f"An unexpected error occurred: {e}")
                raise

    def translate_words(self, words: List[str], **kwargs) -> List[str]:
        """
        Translate a batch of words together by providing them in a list

        @param words: list of words you want to translate
        @return: list of translated words
        """
        if not words:
            raise ValueError("Input words list cannot be empty.")

        translated_words = []
        for word in words:
            translated_words.append(self.translate(text=word, **kwargs))
        return translated_words
