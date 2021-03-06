import selenium.webdriver.support.expected_conditions as EC
from selenium.webdriver import firefox
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
    InvalidArgumentException,
)
from typing import List, Dict, Union, Text
import pandas as pd
from loguru import logger
from datetime import datetime
import sqlite3
from configparser import ConfigParser
import re

datetime_now = datetime.now().strftime("%Y-%m-%d").replace("-", "_")
logger.add(f"./logs/{datetime_now}_file.log")

firefox_webdriver = firefox.webdriver.WebDriver
firefox_webelements = firefox.webelement.FirefoxWebElement


class AcordaosTCU:
    # parametros de configuracao
    config = ConfigParser()
    config.read("config.ini")
    table = config["db"]["tablename"]
    dbname = config["db"]["name"]

    def __init__(self, driver: firefox_webdriver):
        if not isinstance(driver, firefox_webdriver):
            raise TypeError("A classe deve ser iniciada com um webdriver firefox.")
        self.driver = driver
        self.conn, self.cursor = AcordaosTCU.initiate_db()

    def get_urls(self, **kwargs):
        # seleciona apenas as urns que não foram coletadas
        alter_query = kwargs.get("alter_query", None)
        if not alter_query:
            query_string = (
                f"SELECT url_lexml from {AcordaosTCU.table} where was_downloaded = 0"
            )
            self.urls = AcordaosTCU.query_db(query_string, self.cursor)
        else:
            self.urls = AcordaosTCU.query_db(alter_query, self.cursor)

    def parse_urls(self):
        for urls in self.urls:
            for tupurl in reversed(urls):
                url = tupurl[0]
                self.driver.get(url)
                # localiza no dom o container de "Outras Publicações"
                target_class = "panel-body"
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.CLASS_NAME, target_class))
                    )
                except (NoSuchElementException, TimeoutException) as error:
                    logger.warning("Não foi encontrado o elemento Outras Publicações.")
                else:
                    target_container = self.driver.find_elements_by_class_name(
                        target_class
                    )

                # coleta os links originais do normativo
                filter_elems = self.filter_elements_of_interest(
                    target_container, "Tribunal de Contas da União (text/html)"
                )
                if filter_elems:
                    if len(filter_elems) > 1:
                        logger.debug("Há mais de um elemento no filtro.")
                    for elem in filter_elems:
                        href = elem.find_elements_by_class_name("noprint")[
                            0
                        ].get_attribute("href")
                        self.driver.get(href)
                        # identificar se o elemento de ajuda está presente na página
                        pop_up_classname = (
                            "body > app-root:nth-child(1) > ajuda:nth-child(3)"
                        )
                        try:
                            WebDriverWait(self.driver, 10).until(
                                EC.visibility_of(
                                    self.driver.find_element_by_css_selector(
                                        pop_up_classname
                                    )
                                )
                            )
                        except (NoSuchElementException, TimeoutException) as error:
                            logger.warning(
                                "Não foi encontrado elemento de ajuda na página."
                            )
                            pass
                        else:
                            elemento_ajuda = self.driver.find_element_by_css_selector(
                                pop_up_classname
                            )
                            # fecha o elemento de ajuda
                            try:
                                WebDriverWait(self.driver, 10).until(
                                    EC.invisibility_of_element_located(
                                        (By.CLASS_NAME, "tcu-spinner ng-star-inserted")
                                    )
                                )
                            except (NoSuchElementException, TimeoutException) as error:
                                logger.warning(
                                    "Não foi encontrado o elemento tcu-spinner ng-star-inserted."
                                )
                            else:
                                try:
                                    WebDriverWait(self.driver, 10).until(
                                        EC.visibility_of(
                                            self.driver.find_element_by_class_name(
                                                "modal-close"
                                            )
                                        )
                                    )
                                except (
                                    NoSuchElementException,
                                    TimeoutException,
                                ) as error:
                                    pass
                                else:
                                    elemento_ajuda.find_element_by_class_name(
                                        "modal-close"
                                    ).click()
                            # coleta os dados de interesse
                            dados_acordao = self.coleta_dados_pagina_acordao(
                                self.driver
                            )
                            dados_acordao["url_tcu"] = href
                            dados_acordao["urn"] = AcordaosTCU.search_for_urn(url)
                            dados_acordao = {
                                key: str(value).replace("\n", "").replace("'", " ")
                                for key, value in dados_acordao.items()
                            }
                            # atualiza o banco de dados
                            AcordaosTCU.update_a_record(dados_acordao, self.cursor)
                            self.conn.commit()
                            logger.info(f"Finalizado a coleta do link {url}.")
                else:
                    logger.info("Não há links originais a serem parseados.")
        # encerra as conexões com webdriver e banco de dados.
        self.driver.close()
        self.conn.close()

    @staticmethod
    def filter_elements_of_interest(
        webelements: firefox_webelements, substring: Text
    ) -> Union[List[firefox_webelements], None]:
        if not all(isinstance(elem, firefox_webelements) for elem in webelements):
            raise TypeError(
                "Todos os elementos da lista precisam do tipo FirefoxWebElement"
            )
        str_to_match = substring
        filter_only_elements_of_interest = [
            elem for elem in webelements if str_to_match in elem.text
        ]
        return filter_only_elements_of_interest

    @staticmethod
    def coleta_dados_pagina_acordao(browser: firefox_webdriver) -> Dict[str, str]:
        if not isinstance(browser, firefox_webdriver):
            raise TypeError("A função deve receber um firefox webdriver.")
        mapping_dom_id_acordao = {
            "numero_acordao": "conteudo_numero_acordao",
            "relator": "conteudo_relator",
            "processo": "conteudo_processo",
            "tipo_processo": "conteudo_tipo_processo",
            "data_sessao": "conteudo_data_sessao",
            "numero_ata": "conteudo_numero_ata",
            "interessado_reponsavel_recorrente": "conteudo_interessado",
            "entidade": "conteudo_entidade",
            "representante_mp": "conteudo_representante_mp",
            "unidade_tecnica": "conteudo_unidade_tecnica",
            "repr_legal": "conteudo_representante_leval",
            "assunto": "conteudo_assunto",
            "sumario": "conteudo_sumario",
            "acordao": "conteudo_acordao",
            "quorum": "conteudo_quorum",
            "relatorio": "conteudo_relatorio",
            "voto": "conteudo_voto",
        }
        container = {
            "numero_acordao": "",
            "numero_acordao_href": "",
            "relator": "",
            "processo": "",
            "processo_href": "",
            "tipo_processo": "",
            "data_sessao": "",
            "numero_ata": "",
            "numero_ata_href": "",
            "interessado_reponsavel_recorrente": "",
            "entidade": "",
            "representante_mp": "",
            "unidade_tecnica": "",
            "repr_legal": "",
            "assunto": "",
            "sumario": "",
            "acordao": "",
            "quorum": "",
            "relatorio": "",
            "voto": "",
        }
        # coletar dados da página
        ##numero do acordao
        try:
            elem_numero_acordao = browser.find_element_by_id(
                mapping_dom_id_acordao["numero_acordao"]
            )
        except NoSuchElementException:
            container["numero_acordao"] = None
        else:
            container["numero_acordao"] = elem_numero_acordao.text
        ##numero acordao href
        try:
            num_acordao_href = AcordaosTCU.get_a_tag(elem_numero_acordao)
        except (NoSuchElementException, UnboundLocalError):
            container["numero_acordao_href"] = None
        else:
            if num_acordao_href:
                container["numero_acordao_href"] = num_acordao_href
            else:
                container["numero_acordao_href"] = None
        ##relator
        try:
            elem_relator = browser.find_element_by_id(
                mapping_dom_id_acordao["relator"]
            ).text
        except NoSuchElementException:
            container["relator"] = None
        else:
            container["relator"] = elem_relator
        ##processo
        try:
            elem_processo = browser.find_element_by_id(
                mapping_dom_id_acordao["processo"]
            )
        except NoSuchElementException:
            container["processo"] = None
        else:
            container["processo"] = elem_processo.text
        ##processo href
        try:
            processo_href = AcordaosTCU.get_a_tag(elem_processo)
        except (NoSuchElementException, UnboundLocalError):
            container["processo_href"] = None
        else:
            if processo_href:
                container["processo_href"] = processo_href
            else:
                container["processo_href"] = None
        ##tipo de processo
        try:
            elem_tipo_processo = browser.find_element_by_id(
                mapping_dom_id_acordao["tipo_processo"]
            ).text
        except NoSuchElementException:
            container["tipo_processo"] = None
        else:
            container["tipo_processo"] = elem_tipo_processo
        ##data sessão
        try:
            elem_data_sessao = browser.find_element_by_id(
                mapping_dom_id_acordao["data_sessao"]
            ).text
        except NoSuchElementException:
            container["data_sessao"] = None
        else:
            container["data_sessao"] = elem_data_sessao
        ##numero_da_ata
        try:
            elem_numero_ata = browser.find_element_by_id(
                mapping_dom_id_acordao["numero_ata"]
            )
        except NoSuchElementException:
            container["numero_ata"] = None
        else:
            container["numero_ata"] = elem_numero_ata.text
        ##numero da ata href
        try:
            numero_ata_href = AcordaosTCU.get_a_tag(elem_numero_ata)
        except (NoSuchElementException, UnboundLocalError):
            container["numero_ata_href"] = None
        else:
            if numero_ata_href:
                container["numero_ata_href"] = numero_ata_href
            else:
                container["numero_ata_href"] = None
        ##interessado
        try:
            elem_interessado = browser.find_element_by_id(
                mapping_dom_id_acordao["interessado_reponsavel_recorrente"]
            ).text
        except NoSuchElementException:
            container["interessado_reponsavel_recorrente"] = None
        else:
            container["interessado_reponsavel_recorrente"] = elem_interessado
        ##entidade
        try:
            elem_entidade = browser.find_element_by_id(
                mapping_dom_id_acordao["entidade"]
            ).text
        except NoSuchElementException:
            container["entidade"] = None
        else:
            container["entidade"] = elem_entidade
        ##representante_mp
        try:
            elem_repr_mp = browser.find_element_by_id(
                mapping_dom_id_acordao["representante_mp"]
            ).text
        except NoSuchElementException:
            container["representante_mp"] = None
        else:
            container["representante_mp"] = elem_repr_mp
        ##unidade tecnica
        try:
            elem_unidade_tec = browser.find_element_by_id(
                mapping_dom_id_acordao["unidade_tecnica"]
            ).text
        except NoSuchElementException:
            container["unidade_tecnica"] = None
        else:
            container["unidade_tecnica"] = elem_unidade_tec
        ##representante legal
        try:
            elem_repr_legal = browser.find_element_by_id(
                mapping_dom_id_acordao["repr_legal"]
            ).text
        except NoSuchElementException:
            container["repr_legal"] = None
        else:
            container["repr_legal"] = elem_repr_legal
        ##assunto
        try:
            elem_assunto = browser.find_element_by_id(
                mapping_dom_id_acordao["assunto"]
            ).text
        except NoSuchElementException:
            container["assunto"] = None
        else:
            container["assunto"] = elem_assunto
        ##sumário
        try:
            elem_sumario = browser.find_element_by_id(
                mapping_dom_id_acordao["sumario"]
            ).text
        except NoSuchElementException:
            container["sumario"] = None
        else:
            container["sumario"] = elem_sumario
        ##acórdão
        try:
            elem_acordao = browser.find_element_by_id(
                mapping_dom_id_acordao["acordao"]
            ).text
        except NoSuchElementException:
            container["acordao"] = None
        else:
            container["acordao"] = elem_acordao
        ##quorum
        try:
            elem_quorum = browser.find_element_by_id(
                mapping_dom_id_acordao["quorum"]
            ).text
        except NoSuchElementException:
            container["quorum"] = None
        else:
            container["quorum"] = elem_quorum
        ##relatorio
        try:
            elem_relatorio = browser.find_element_by_id(
                mapping_dom_id_acordao["relatorio"]
            ).text
        except NoSuchElementException:
            container["relatorio"] = None
        else:
            container["relatorio"] = elem_relatorio
        ##voto
        try:
            elem_voto = browser.find_element_by_id(mapping_dom_id_acordao["voto"]).text
        except NoSuchElementException:
            container["voto"] = None
        else:
            container["voto"] = elem_voto

        return container

    @staticmethod
    def get_a_tag(webelement: firefox_webelements) -> Union[Text, None]:
        if not isinstance(webelement, firefox_webelements):
            raise TypeError("O input da função deve ser um firefox webelement.")
        # verifica se há uma a tag presente na hierarquia do webelement
        is_a_tag_present = webelement.find_elements_by_tag_name("a")
        if is_a_tag_present:
            for elem in is_a_tag_present:
                href = elem.get_attribute("href")
                if href:
                    return href
                else:
                    return None
        else:
            return None

    @staticmethod
    def initiate_db() -> sqlite3.Cursor:
        """
        Conecta no banco sqlite

        Atributos:
            strcnx: string de conexão.
        """
        conn = sqlite3.connect(AcordaosTCU.dbname)
        cur = conn.cursor()
        return conn, cur

    @staticmethod
    def query_db(query_string: str, cursor: sqlite3.Cursor):
        yield cursor.execute(query_string).fetchall()

    @staticmethod
    def update_a_record(container: Dict, cursor: sqlite3.Cursor) -> None:
        parse_values_to_update = AcordaosTCU.format_update_string(container)
        update_string = f"""
        UPDATE {AcordaosTCU.table}
        SET {parse_values_to_update} 
        WHERE urn = '{container['urn']}'
        """
        try:
            cursor.execute(update_string)
        except sqlite3.OperationalError as error:
            raise ValueError(update_string)

    @staticmethod
    def format_update_string(container: Dict) -> str:
        update_string = ""
        for key, value in container.items():
            if key != "urn":
                if value and value != "None":
                    update_string += f"{key} = '{value}', "
        today = datetime.now().strftime("%Y-%m-%d")
        update_string += f"was_downloaded = 1, downloaded_at = '{today}'"
        update_string = update_string.strip()
        return update_string

    @staticmethod
    def search_for_urn(url: str) -> str:
        """
        Filtra a urn da url que foi realizado o get
        """
        re_pattern = r"http(s)?:\/\/www\.\w+\.\w+\.\w+\/\w+\/"
        look_for_urn = re.search(re_pattern, url).span()[1]
        urn = url[look_for_urn:]
        return urn
