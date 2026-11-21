"""Amostragem estratificada que inclui código da aplicação e de bibliotecas.

Diferentemente de ``filtro.py``, classes fora do package name do APK não são
descartadas. A otimização preserva uma observação por APK e prioriza, nesta
ordem: cobertura de specifications, diversidade de domínios externos e uma
distribuição de specs/origens próxima à população elegível.
"""

from __future__ import annotations

from cognalys.workspace import workspace_root
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import pandas as pd
import pulp


SYNTHETIC_METHOD = re.compile(
    r"<clinit>|invokeSuspend|\$lambda|\([^)]*\.(?:kt|java):\d+\)$"
)


def pacote(apks: pd.Series) -> pd.Series:
    return apks.str.rsplit("/", n=1).str[-1].str.replace(
        r"_[^_]+\.apk$", "", regex=True
    )


def dominio_classe(nome: object, segmentos: int = 3) -> str:
    """Retorna um domínio estável sem presumir que toda classe é Java válida."""
    if not isinstance(nome, str) or not nome.strip():
        return "unknown"
    normalizado = nome.strip().replace("/", ".").replace("$", ".")
    partes = [parte for parte in normalizado.split(".") if parte]
    return ".".join(partes[:segmentos]) if partes else "unknown"


def pertence_ao_aplicativo(classe: object, package_name: object) -> bool:
    if not isinstance(classe, str) or not isinstance(package_name, str):
        return False
    return classe == package_name or classe.startswith(package_name + ".")


def adicionar_origem(df: pd.DataFrame) -> pd.DataFrame:
    resultado = df.copy()
    proprio = [
        pertence_ao_aplicativo(classe, base)
        for classe, base in zip(resultado["class"], resultado["pacote"])
    ]
    resultado["origem_classe"] = [
        "aplicacao" if valor else "biblioteca_ou_externo" for valor in proprio
    ]
    resultado["dominio_classe"] = resultado["class"].map(dominio_classe)
    return resultado


def selecionar_amostra(df: pd.DataFrame) -> pd.DataFrame:
    """Seleciona uma linha por APK com cobertura e diversidade lexicográficas."""
    elegiveis = df.dropna(subset=["apk", "spec", "class", "method"]).copy()
    elegiveis = elegiveis[
        ~elegiveis["method"].astype(str).str.contains(SYNTHETIC_METHOD, na=True)
    ]
    if elegiveis.empty:
        return elegiveis

    # Preservar origem e domínio aqui é essencial: drop_duplicates(apk, spec)
    # poderia manter apenas a classe da aplicação e apagar o candidato externo.
    candidatos = elegiveis.drop_duplicates(
        ["apk", "spec", "origem_classe", "dominio_classe"]
    ).reset_index()
    apks = list(candidatos["apk"].unique())
    specs = list(candidatos["spec"].unique())
    dominios_externos = list(candidatos.loc[
        candidatos["origem_classe"] == "biblioteca_ou_externo",
        "dominio_classe",
    ].unique())
    origens = list(candidatos["origem_classe"].unique())
    linhas = list(candidatos.index)
    n = len(apks)

    modelo = pulp.LpProblem("amostragem_com_bibliotecas", pulp.LpMaximize)
    x = pulp.LpVariable.dicts("linha", linhas, cat="Binary")
    y_spec = pulp.LpVariable.dicts("cobre_spec", specs, cat="Binary")
    y_dominio = pulp.LpVariable.dicts(
        "cobre_dominio_externo", dominios_externos, cat="Binary"
    )
    desvio_spec = pulp.LpVariable.dicts("desvio_spec", specs, lowBound=0)
    desvio_origem = pulp.LpVariable.dicts("desvio_origem", origens, lowBound=0)

    # Pesos tornam as prioridades lexicográficas: nenhuma melhora nos níveis
    # inferiores compensa perder uma specification coberta; depois vem a
    # diversidade externa e, por fim, a representatividade da população.
    peso_dominio = 2 * n + 1
    peso_spec = (len(dominios_externos) + 1) * peso_dominio + 2 * n + 1
    modelo += (
        peso_spec * pulp.lpSum(y_spec.values())
        + peso_dominio * pulp.lpSum(y_dominio.values())
        - pulp.lpSum(desvio_spec.values())
        - pulp.lpSum(desvio_origem.values())
    )

    for _, grupo in candidatos.groupby("apk"):
        modelo += pulp.lpSum(x[i] for i in grupo.index) == 1

    for spec, grupo in candidatos.groupby("spec"):
        quantidade = pulp.lpSum(x[i] for i in grupo.index)
        alvo = n * (elegiveis["spec"] == spec).mean()
        modelo += quantidade >= y_spec[spec]
        modelo += desvio_spec[spec] >= quantidade - alvo
        modelo += desvio_spec[spec] >= alvo - quantidade

    externos = candidatos[
        candidatos["origem_classe"] == "biblioteca_ou_externo"
    ]
    for dominio, grupo in externos.groupby("dominio_classe"):
        modelo += pulp.lpSum(x[i] for i in grupo.index) >= y_dominio[dominio]

    for origem, grupo in candidatos.groupby("origem_classe"):
        quantidade = pulp.lpSum(x[i] for i in grupo.index)
        alvo = n * (elegiveis["origem_classe"] == origem).mean()
        modelo += desvio_origem[origem] >= quantidade - alvo
        modelo += desvio_origem[origem] >= alvo - quantidade

    status = modelo.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(
            f"O otimizador não encontrou solução ótima: {pulp.LpStatus[status]}"
        )
    indices = [
        candidatos.loc[i, "index"] for i in linhas if pulp.value(x[i]) > 0.5
    ]
    return elegiveis.loc[indices].copy()


CATALOGO_URL = "https://f-droid.org/repo/index-v1.json"


def carregar_catalogo_fdroid(
    snapshot: Path | None = None, baixar: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Catálogo do F-Droid e a procedência do que foi lido.

    O catálogo era baixado ao vivo a cada execução, de modo que a mesma
    entrada e a mesma semente produziam amostras diferentes conforme o
    índice mudava, e nada registrava qual versão havia sido usada. Com um
    snapshot, a amostra volta a ser reproduzível; sem ele, o download é
    arquivado com data e hash para que passe a ser.
    """
    procedencia: dict = {"fonte": "", "url": CATALOGO_URL, "sha256": "",
                         "obtido_em": "", "aplicativos": 0}
    if snapshot is not None and Path(snapshot).is_file():
        bruto = Path(snapshot).read_bytes()
        procedencia["fonte"] = f"snapshot:{Path(snapshot)}"
    elif baixar:
        with urlopen(CATALOGO_URL, timeout=120) as resposta:
            bruto = resposta.read()
        procedencia["fonte"] = "download"
        procedencia["obtido_em"] = datetime.now(timezone.utc).isoformat()
        if snapshot is not None:
            Path(snapshot).parent.mkdir(parents=True, exist_ok=True)
            Path(snapshot).write_bytes(bruto)
            procedencia["fonte"] = f"download,arquivado:{Path(snapshot)}"
    else:
        raise RuntimeError(
            "sem snapshot do catálogo e download desativado: a amostra não "
            "pode ser montada sem registrar de onde veio o catálogo"
        )
    procedencia["sha256"] = hashlib.sha256(bruto).hexdigest()
    apps = json.loads(bruto.decode("utf-8"))["apps"]
    procedencia["aplicativos"] = len(apps)
    catalogo = pd.DataFrame(
        {"pacote": app["packageName"], "repo_url": app.get("sourceCode")}
        for app in apps
    ).drop_duplicates("pacote")
    return catalogo, procedencia


def achados_unicos(df: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por achado, não por observação.

    O CSV bruto registra uma linha por execução do detector, de modo que o
    mesmo achado aparece várias vezes. As metas de distribuição eram
    calculadas sobre essas observações, isto é, sobre uma população em que
    um achado repetido pesa mais que um achado único. Quando a pergunta é
    sobre achados, a população tem de ser deduplicada antes; quando é sobre
    observações de runtime, isso precisa ser dito explicitamente.
    """
    chaves = [coluna for coluna in ("unique_msg",) if coluna in df.columns]
    if not chaves:
        chaves = [
            coluna for coluna in ("apk", "class", "method", "message")
            if coluna in df.columns
        ]
    return df.drop_duplicates(chaves) if chaves else df


def filtrar_e_amostrar(
    entrada: Path, saida: Path, snapshot_catalogo: Path | None = None,
    baixar_catalogo: bool = True,
) -> pd.DataFrame:
    df = pd.read_csv(entrada)
    obrigatorias = {"apk", "spec", "class", "method"}
    ausentes = sorted(obrigatorias - set(df.columns))
    if ausentes:
        raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(ausentes)}")

    df["pacote"] = pacote(df["apk"])
    catalogo, procedencia_catalogo = carregar_catalogo_fdroid(
        snapshot_catalogo, baixar_catalogo
    )
    catalogo = catalogo.rename(columns={"repo_url": "repo_url_catalogo"})
    df = df.merge(catalogo, on="pacote", how="left")
    if "repo_url" in df.columns:
        # A entrada pode ser a saída de filtro.py/filtro_v2. Preserve o valor
        # já enriquecido e consulte o catálogo apenas para preencher lacunas.
        existente = df["repo_url"].replace(r"^\s*$", pd.NA, regex=True)
        df["repo_url"] = existente.fillna(df["repo_url_catalogo"])
    else:
        df["repo_url"] = df["repo_url_catalogo"]
    df = df.drop(columns=["repo_url_catalogo"])
    df["repo"] = (
        df["repo_url"].str.removesuffix(".git").str.split("/").str[-2:].str.join("/")
    )
    df["apk_url"] = "https://f-droid.org/repo/" + df["apk"]
    df = adicionar_origem(df)
    externos_elegiveis = int(
        (df["origem_classe"] == "biblioteca_ou_externo").sum()
    )
    if externos_elegiveis == 0:
        print(
            "WARNING: o CSV de entrada não contém classes externas. Ele pode "
            "já ter sido filtrado por filtro.py/filtro_v2; use o CSV bruto "
            "(por exemplo data/raw/errors.csv) para amostrar bibliotecas.",
            flush=True,
        )
    amostra = selecionar_amostra(df)
    amostra.to_csv(saida, index=False)

    # Sem isto a amostra não é reconstituível: o catálogo muda, o solver tem
    # empates e a população sobre a qual as metas foram calculadas não fica
    # registrada em lugar nenhum.
    unicos = achados_unicos(df)
    procedencia = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "entrada": {
            "arquivo": str(entrada),
            "sha256": hashlib.sha256(Path(entrada).read_bytes()).hexdigest(),
            "linhas": int(len(df)),
        },
        "saida": {
            "arquivo": str(saida),
            "sha256": hashlib.sha256(Path(saida).read_bytes()).hexdigest(),
            "linhas": int(len(amostra)),
        },
        "catalogo_fdroid": procedencia_catalogo,
        "populacao": {
            "observacoes_elegiveis": int(len(df)),
            "achados_unicos": int(len(unicos)),
            "metas_calculadas_sobre": "observacoes_elegiveis",
            "nota": (
                "As metas de distribuição do otimizador são calculadas sobre "
                "observações elegíveis, em que um achado repetido pelo "
                "detector pesa mais de uma vez. Para uma pergunta sobre "
                "achados, compare com achados_unicos antes de descrever a "
                "amostra como representativa."
            ),
            "specs_observacoes": {
                str(k): int(v) for k, v in df["spec"].value_counts().items()
            },
            "specs_achados_unicos": {
                str(k): int(v) for k, v in unicos["spec"].value_counts().items()
            },
        },
        "selecao": {
            "metodo": "programacao_linear_inteira",
            "solver": "CBC (pulp.PULP_CBC_CMD)",
            "objetivo": "lexicografico: cobertura de specs > diversidade de "
                        "dominios externos > proximidade da distribuicao",
            "aleatoriedade": "nenhuma; nao e amostragem probabilistica",
            "desempate": "resolvido pelo solver, nao por sorteio; duas "
                         "execucoes com a mesma entrada e o mesmo catalogo "
                         "devolvem a mesma solucao otima, mas outra solucao "
                         "de mesmo valor seria igualmente otima",
        },
        "origem_classe": {
            "metodo": "prefixo de package do APK (pertence_ao_aplicativo)",
            "limitacao": "um package diferente nao comprova dependencia "
                         "externa; valide com coordenada Gradle resolvida "
                         "antes de relatar a divisao aplicacao/biblioteca",
            "distribuicao_amostra": {
                str(k): int(v)
                for k, v in amostra["origem_classe"].value_counts().items()
            },
        },
    }
    caminho_procedencia = saida.with_suffix(".proveniencia.json")
    caminho_procedencia.write_text(
        json.dumps(procedencia, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    externas = amostra["origem_classe"] == "biblioteca_ou_externo"
    print(
        f"{len(amostra)} APKs, {amostra['spec'].nunique()} specs, "
        f"{int(externas.sum())} achados externos em "
        f"{amostra.loc[externas, 'dominio_classe'].nunique()} domínios, "
        f"{amostra['repo_url'].notna().sum()} repositórios -> {saida}"
    )
    print(f"procedência -> {caminho_procedencia}")
    return amostra


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Amostra um achado por APK sem excluir classes de bibliotecas, "
            "maximizando cobertura de specs e diversidade externa."
        )
    )
    parser.add_argument("entrada", type=Path)
    parser.add_argument("-o", "--saida", type=Path)
    parser.add_argument(
        "--snapshot-catalogo", type=Path,
        help=("Arquivo do índice do F-Droid. Se existir, é lido; se não, o "
              "download é arquivado nele para que a amostra seja "
              "reproduzível."),
    )
    parser.add_argument(
        "--sem-download", action="store_true",
        help="Falha em vez de baixar o catálogo; exige --snapshot-catalogo.",
    )
    args = parser.parse_args()
    saida = args.saida or (
        workspace_root()
        / "data"
        / "processed"
        / f"{args.entrada.stem}_amostragem_v3.csv"
    )
    saida.parent.mkdir(parents=True, exist_ok=True)
    filtrar_e_amostrar(
        args.entrada, saida, args.snapshot_catalogo, not args.sem_download,
    )


if __name__ == "__main__":
    main()
