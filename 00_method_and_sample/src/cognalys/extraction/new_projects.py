import requests
import time
import pandas as pd
import json
import os
from datetime import datetime

FDROID_INDEX_URL = "https://f-droid.org/repo/index-v2.json"
GITHUB_API = "https://api.github.com/repos"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")

HEADERS = {}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


def get_fdroid_projects():
    print("Baixando índice do F-Droid...")
    resp = requests.get(FDROID_INDEX_URL)
    data = resp.json()

    apps = data.get("packages", {})
    repos = {}

    for pkg_name, pkg_data in apps.items():
        metadata = pkg_data.get("metadata", {})

        source = metadata.get("sourceCode")
        last_updated = metadata.get("lastUpdated")

        if not (source and "github.com" in source and last_updated):
            continue

        apk_url = None
        versions = pkg_data.get("versions")

        # 🔥 NOVA LÓGICA: percorre TODAS versões até achar APK
        if isinstance(versions, dict):
            for v in versions.values():
                file_info = v.get("file", {})
                apk_name = file_info.get("name")

                if apk_name and apk_name.endswith(".apk"):
                    apk_name = apk_name.lstrip("/")
                    apk_url = f"https://f-droid.org/repo/{apk_name}"
                    break  # 🔥 achou → para

        # 🔥 garante que não perde APK válido
        if source not in repos:
            repos[source] = {
                "lastUpdated": last_updated,
                "apk_url": apk_url
            }
        else:
            # mantém o mais recente
            if repos[source]["lastUpdated"] < last_updated:
                repos[source]["lastUpdated"] = last_updated

            # 🔥 mantém APK válido se existir
            if apk_url and not repos[source]["apk_url"]:
                repos[source]["apk_url"] = apk_url

    # transforma em lista
    result = [
        {
            "repo_url": url,
            "lastUpdated": info["lastUpdated"],
            "apk_url": info["apk_url"]
        }
        for url, info in repos.items()
    ]

    print(f"Total de repositórios únicos: {len(result)}")
    return result

# ----------------------------
# 2. Extrai owner/repo
# ----------------------------
def extract_github_repo(url):
    try:
        parts = url.replace("https://github.com/", "").split("/")
        return parts[0], parts[1]
    except:
        return None, None


# ----------------------------
# 3. Consulta PR mais recente
# ----------------------------
def get_latest_pr(owner, repo):
    url = f"{GITHUB_API}/{owner}/{repo}/pulls?state=all&sort=updated&per_page=1"
    resp = requests.get(url, headers=HEADERS)

    if resp.status_code != 200:
        return None

    data = resp.json()
    return data[0] if data else None


# ----------------------------
# 4. Pipeline principal
# ----------------------------
def main():
    repos = get_fdroid_projects()

    # 🔥 ordena por atividade recente no F-Droid
    repos_sorted = sorted(
        repos,
        key=lambda x: x["lastUpdated"],
        reverse=True
    )

    # 🔥 pool inicial (ajustável)
    candidate_pool = repos_sorted[:1000]

    print(f"Analisando {len(candidate_pool)} projetos recentes...")

    results = []

    for i, item in enumerate(candidate_pool):
        repo_url = item["repo_url"]
        apk_url = item["apk_url"]

        owner, repo = extract_github_repo(repo_url)
        if not owner:
            continue

        print(f"[{i+1}/{len(candidate_pool)}] {owner}/{repo}")

        pr = get_latest_pr(owner, repo)

        if pr:
            results.append({
                "repo": f"{owner}/{repo}",
                "repo_url": repo_url,  
                "apk_url": apk_url,   
                "fdroid_last_updated": item["lastUpdated"],
                "pr_title": pr.get("title"),
                "pr_updated_at": pr.get("updated_at"),
                "author": pr.get("user", {}).get("login"),
                "state": pr.get("state")
            })

        time.sleep(0.5)

    # ----------------------------
    # 5. Ranking final
    # ----------------------------
    results.sort(key=lambda x: x["pr_updated_at"], reverse=True)

    top_400 = results[:400]

    # ----------------------------
    # 6. Export JSON
    # ----------------------------
    with open("fdroid_prs_top400.json", "w", encoding="utf-8") as f:
        json.dump(top_400, f, ensure_ascii=False, indent=2)

    # ----------------------------
    # 7. Export CSV
    # ----------------------------
    df = pd.DataFrame(top_400)
    df.to_csv("fdroid_prs_top400.csv", index=False)
    df.to_excel("fdroid_prs_top400.xlsx", index=False)

    print("\n✅ Arquivos gerados:")
    print(" - fdroid_prs_top400.json")
    print(" - fdroid_prs_top400.csv")
    print(" - fdroid_prs_top400.xlsx")


if __name__ == "__main__":
    main()
