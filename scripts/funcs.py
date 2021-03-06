import os
import pandas as pd
import json
import re
import gc
from configparser import ConfigParser
from pathlib import Path
from typing import List, Dict, Union, Text, Tuple, Iterable
from numbers import Number
from pathlib import Path
from selenium.webdriver import Firefox
from selenium.webdriver import firefox
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import NoSuchElementException
from scripts.crawler import AcordaosTCU
import sqlite3

firefox_webelements = firefox.webelement.FirefoxWebElement
firefox_webdriver = firefox.webdriver.WebDriver


def parse_json_year_date(year: Number, fullpath: Path) -> Union[Path, None]:
    """
    Filtra os arquivos json por ano.
    """
    if not isinstance(fullpath, Path):
        raise TypeError("O parâmetro path deve do tipo Path.")
    pattern_finder = re.search(f"_{year}\.json", fullpath.name)
    if pattern_finder:
        return fullpath
    else:
        return None


def load_into_dataframe(jsonFile: List[Dict]) -> pd.DataFrame:
    """
    Cria uma DataFrame a partir de uma lista de dicionários (JSON).
    """
    # container para armazenar arquivos json
    container_of_json = []
    for file in jsonFile:
        with open(file, "r", encoding="utf8") as f:
            d = json.load(f)
            container_of_json.append(d)
    # container of dataframes
    container_of_dataframes = []
    for data in container_of_json:
        df = pd.read_json(json.dumps(data), orient="records", encoding="utf8")
        container_of_dataframes.append(df)
    df = pd.concat(container_of_dataframes)
    return df


def get_urn(pattern: str, df: pd.DataFrame) -> Dict:
    """
    Recebe padrão de urn e coleta todos as ocorrências no dataframe.
    """
    urn_container = {}
    for index, row in df.iterrows():
        if type(row["urn"]) == list:
            for data in row["urn"]:
                if pattern in data:
                    if pattern in urn_container:
                        continue
                    else:
                        urn_container[row["urn"]] = row["url"]
        else:
            if pattern in row["urn"]:
                if pattern in urn_container:
                    continue
                else:
                    urn_container[row["urn"]] = row["url"]
    return urn_container


def select_files_based_on_year(path: Path, year: str) -> List[Path]:
    """
    Seleciona os arquivos baseado no ano indicado em seus respectivos nomes.
    """
    if not isinstance(path, Path):
        raise TypeError("O parâmetro path deve do tipo Path.")
    container_of_json_year = []
    path_to_str = str(path.absolute())
    for dirname, _, filenames in os.walk(path_to_str):
        for filename in filenames:
            path_filename = Path(os.path.join(dirname, filename))
            check_pattern = parse_json_year_date(year, path_filename)
            if check_pattern:
                container_of_json_year.append(path_filename)
    return container_of_json_year


def pipeline_to_get_urn(
    path: Path, years: List[str], patterns: List[str]
) -> (List[Dict], List[int]):
    """
    Pipeline para coletar as urns de um determinado padrão ao longo de vários arquivos.
    
    Atributos:
        path: diretório onde estão os arquivos json
        years: list de anos que se deseja coletar os dados
        pattern: a substring oriunda de uma URN que se deseja buscar
    """
    if not isinstance(path, Path):
        raise TypeError("O parâmetro path deve do tipo Path.")
    container = []
    if not isinstance(years, List):
        raise TypeError("O parâmetro years precisa ser uma lista.")
    if not isinstance(patterns, List):
        raise TypeError("O parâmetro patterns precisa ser uma lista.")
    #criar container para armazenar os anos que possuem dados
    filtered_years = []
    for year in years:
        container_of_json_year = select_files_based_on_year(path, year)
        if not container_of_json_year:
            print(f"Não há dados relativos ao {path} e {year}.")
            continue
        # sort by filename
        container_of_json_year = sorted(
            container_of_json_year, key=lambda x: int(x.name.split("_")[0])
        )
        # carrega os dados
        df = load_into_dataframe(container_of_json_year)
        for pattern in patterns:
            print(
                f"Iniciando a coleta das urn para o padrão {pattern} na base anual {year}."
            )
            urn_list = get_urn(pattern, df)
            container.append(urn_list)
            del urn_list
        filtered_years.append(year)
        del df
        gc.collect()
    return container, filtered_years


def create_df_for_urn_data_and_save(data: Dict, filename: str) -> None:
    x = pd.DataFrame.from_dict(data, orient="index")
    x.reset_index(inplace=True)
    x.columns = ["urn", "url"]
    x = x[["urn", "url"]]
    path_to_save = Path(f"./data/")
    path_to_save.mkdir(parents=True, exist_ok=True)
    path_to_save = path_to_save / f"{filename}.csv"
    x.to_csv(path_to_save, encoding="utf8", index=False)


def initiate_webdriver() -> firefox_webdriver:
    config = ConfigParser()
    config.read("config.ini")
    driver = config["driver"]["driver"]
    options = Options()
    options.headless = True
    path_to_save_logs = Path(config["driver"]["driver_logs"])
    if not path_to_save_logs.parent.is_dir():
        path_to_save_logs.mkdir(parents=True, exist_ok=True)
    browser = Firefox(
        executable_path=driver, service_log_path=path_to_save_logs, options=options
    )
    return browser


def load_csv_into_db(years: List[int], cursor: sqlite3.Cursor) -> None:
    for year in years:
        df = pd.read_csv(f"./data/tcu_{year}.csv", sep=",", encoding="utf8")
        year_urn_pattern = '[0-9]{4}-[0-9]{2}-[0-9]{2}'
        df['urn_year'] = df['urn'].apply(lambda x: int(re.search(year_urn_pattern,x).group(0)[:4]))
        data_to_insert = [(data.urn, data.url, data.urn_year) for data in df.itertuples()]
        insert_into_db(
            data=data_to_insert,
            table_name="download_acordaos",
            cols_names=["urn", "url_lexml", "urn_year"],
            cursor=cursor,
        )

def load_json_into_db(filename: Path, cursor: sqlite3.Cursor) -> None:
    data_to_insert =  []
    with open(filename, 'r', encoding='utf8') as f:
        d = json.load(f)
        cols_name = list(d[0].keys())
        cols_name.append('urn')
        for data in d:
            data['urn'] = 'NA'
            instance_of_data = tuple(value for value in data.values())
            data_to_insert.append(instance_of_data)
        insert_into_db(
            data=data_to_insert,
            table_name="download_acordaos",
            cols_names=cols_name,
            cursor=cursor,
        )


 

def initiate_db(strcnx: str) -> sqlite3.Cursor:
    """
    Conecta no banco sqlite

    Atributos:
        strcnx: string de conexão.
    """
    strcnx_is_valid = Path(strcnx)
    if not strcnx_is_valid.is_file():
        raise ("O arquivo sqlite3 não existe.")
    conn = sqlite3.connect(strcnx)
    cur = conn.cursor()
    return conn, cur


def insert_into_db(
    data: Tuple, table_name: str, cols_names: List[str], cursor: sqlite3.Cursor
) -> None:
    cols_to_insert = f"({','.join(cols_names)})"
    question_mark_str = f"({','.join(['?' for col in cols_names])})"
    insert_string = (
        f"INSERT INTO {table_name} {cols_to_insert} VALUES {question_mark_str}"
    )
    cursor.executemany(insert_string, data)

def search_for_urn(logmsg: str) -> str:
    """
    Encontra urns nas mensagens de log.
    """
    re_pattern = r'http(s)?:\/\/www\.\w+\.\w+\.\w+\/\w+\/'
    look_for_urn = re.search(re_pattern, logmsg).span()[1]
    urn = logmsg[look_for_urn:-1]
    return urn

def query_db(query_string: str, cursor: sqlite3.Cursor):
    yield cursor.execute(query_string).fetchall()


def mask_cnpj(texto: str) -> Union[str, None]:
    """
    Anonimização de CPF
    """
    formatted_text = texto
    pattern = '[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}'
    cpfinder = re.compile(pattern)
    if isinstance(texto, str):
        is_cpf = cpfinder.findall(texto)
        if is_cpf:
            for cpf in is_cpf:
                masked_cpf = f"XXX-{cpf[4:-3]}-XX"
                formatted_text = formatted_text.replace(cpf, masked_cpf)
            return formatted_text
    else:
        return None

def ResultIter(cursor: sqlite3.Cursor, query: str) -> Union[Iterable, None]:
    'An iterator to keep memory usage down on quering database'
    results = cursor.execute(query).fetchall()
    if not results:
        yield None
    for result in results:
        yield result
