"""base translator class"""

__copyright__ = "Copyright (C) 2020 Nidhal Baccouri"

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Union

from deep_translator.constants import GOOGLE_LANGUAGES_TO_CODES
from deep_translator.exceptions import (
    InvalidSourceOrTargetLanguage,
    LanguageNotSupportedException,
)


class BaseTranslator(ABC):
    """
    Abstract class that serve as a base translator for other different translators
    """

    def __init__(
        self,
        base_url: str = None,
        languages: dict = GOOGLE_LANGUAGES_TO_CODES,
        source: str = "auto",
        target: str = "en",
        payload_key: Optional[str] = None,
        element_tag: Optional[str] = None,
        element_query: Optional[dict] = None,
        **url_params,
    ):
        """
        @param source: source language to translate from
        @param target: target language to translate to
        """
        self._base_url = base_url
        self._languages = languages
        self._supported_languages = list(self._languages.keys())
        if not source:
            raise InvalidSourceOrTargetLanguage(source)
        if not target:
            raise InvalidSourceOrTargetLanguage(target)

        self._source, self._target = self._map_language_to_code(source, target)
        self._url_params = url_params
        self._element_tag = element_tag
        self._element_query = element_query
        self.payload_key = payload_key
        super().__init__()

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, lang):
        self._source = lang

    @property
    def target(self):
        return self._target

    @target.setter
    def target(self, lang):
        self._target = lang

    def _type(self):
        return self.__class__.__name__

    def _map_language_to_code(self, *languages):
        """
        map language to its corresponding code (abbreviation) if the language was passed
        by its full name by the user
        @param languages: list of languages
        @return: mapped value of the language or raise an exception if the language is
        not supported
        """
        for language in languages:
            if language in self._languages.values() or language == "auto":
                yield language
            elif language in self._languages.keys():
                yield self._languages[language]
            else:
                raise LanguageNotSupportedException(
                    language,
                    message=f"No support for the provided language.\n"
                    f"Please select on of the supported languages:\n"
                    f"{self._languages}",
                )

    def _same_source_target(self) -> bool:
        return self._source == self._target

    def get_supported_languages(
        self, as_dict: bool = False, **kwargs
    ) -> Union[list, dict]:
        """
        return the supported languages by the Google translator
        @param as_dict: if True, the languages will be returned as a dictionary
        mapping languages to their abbreviations
        @return: list or dict
        """
        return self._supported_languages if not as_dict else self._languages

    def is_language_supported(self, language: str, **kwargs) -> bool:
        """
        check if the language is supported by the translator
        @param language: a string for 1 language
        @return: bool or raise an Exception
        """
        if (
            language == "auto"
            or language in self._languages.keys()
            or language in self._languages.values()
        ):
            return True
        else:
            return False

    @abstractmethod
    def translate(self, text: str, **kwargs) -> str:
        """
        function to translate a text
        @param text: desired text to translate
        @return: str: translated text
        """
        if is_input_valid(text, max_chars=5000):
            text = text.strip()
            if self._same_source_target() or is_empty(text):
                return text
            self._url_params["tl"] = self._target
            self._url_params["sl"] = self._source

            if self.payload_key:
                self._url_params[self.payload_key] = text

            response = requests.get(
                self._base_url, params=self._url_params, proxies=self.proxies
            )
            if response.status_code == 429:
                raise TooManyRequests()

            if request_failed(status_code=response.status_code):
                raise RequestError()

            soup = BeautifulSoup(response.text, "html.parser")

            element = soup.find(self._element_tag, self._element_query)
            response.close()

            if not element:
                element = soup.find(self._element_tag, self._alt_element_query)
                if not element:
                    raise TranslationNotFound(text)

            translated_text = element.get_text(strip=True)

            # Return the translated text if it's different from the input
            if translated_text != text.strip():
                return translated_text
            else:
                # Handle cases where the translation might be the same as the input
                logging.warning(f"Translation is the same as input for: {text}")
                return translated_text  # Or return text.strip() if you prefer

    def _read_docx(self, f: str):
        import docx2txt

        return docx2txt.process(f)

    def _read_pdf(self, f: str):
        import pypdf

        reader = pypdf.PdfReader(f)
        page = reader.pages[0]
        return page.extract_text()

    def _translate_file(self, path: str, **kwargs) -> str:
        """
        translate directly from file
        @param path: path to the target file
        @type path: str
        @param kwargs: additional args
        @return: str
        """
        if not isinstance(path, Path):
            path = Path(path)

        if not path.exists():
            print("Path to the file is wrong!")
            exit(1)

        ext = path.suffix

        if ext == ".docx":
            text = self._read_docx(f=str(path))

        elif ext == ".pdf":
            text = self._read_pdf(f=str(path))
        else:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()

        return self.translate(text)

    def _translate_batch(self, batch: List[str], **kwargs) -> List[str]:
        """
        translate a list of texts
        @param batch: list of texts you want to translate
        @return: list of translations
        """
        if not batch:
            raise Exception("Enter your text list that you want to translate")
        arr = []
        for i, text in enumerate(batch):
            translated = self.translate(text, **kwargs)
            arr.append(translated)
        return arr
