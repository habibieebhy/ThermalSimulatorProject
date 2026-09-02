#!/usr/bin/env python3

import csv
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://rera.assam.gov.in"
REGISTERED_PROJECTS_URL = (
    "https://rera.assam.gov.in/admincontrol/registered_projects/1"
)
PROJECT_SEARCH_URL = (
    "https://rera.assam.gov.in/assistancecontrol/project_search_public/1"
)

OUTPUT_DIR = Path("rera_output")
OUTPUT_DIR.mkdir(exist_ok=True)

CSV_FILE = OUTPUT_DIR / "assam_rera_2029_projects.csv"

TARGET_DISTRICTS = {
    "KAMRUP",
    "KAMRUP METRO",
}

TARGET_YEAR = 2029

REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.35
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_text(value):
    if value is None:
        return ""

    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def get(session, url):
    """
    GET with retry handling.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()
            return response

        except requests.RequestException as exc:
            print(
                f"  [retry {attempt}/{MAX_RETRIES}] "
                f"{url} -> {exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(attempt * 2)

    return None


def extract_project_id(url):
    if not url:
        return None

    match = re.search(
        r"/project_preview_open/(\d+)",
        url,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1)

    return None


def parse_registered_projects(html):
    """
    Parse the registered-projects table.

    We specifically identify the table containing:
      Registration Certificate Number
      Project Name
      Promoter
      Project Location
      Project District
      View Webpage
    """

    soup = BeautifulSoup(html, "html.parser")

    target_table = None

    for table in soup.find_all("table"):
        headers = [
            clean_text(th.get_text(" ", strip=True)).lower()
            for th in table.find_all("th")
        ]

        required = {
            "registration certificate number",
            "project name",
            "promoter",
            "project location",
            "project district",
            "view webpage",
        }

        if required.issubset(set(headers)):
            target_table = table
            break

    if target_table is None:
        raise RuntimeError(
            "Could not find the registered-projects table."
        )

    headers = [
        clean_text(th.get_text(" ", strip=True))
        for th in target_table.find_all("th")
    ]

    header_map = {
        header.lower(): index
        for index, header in enumerate(headers)
    }

    projects = []

    tbody = target_table.find("tbody")

    if tbody is None:
        print("WARNING: target table has no <tbody>")
        return projects

    rows = tbody.find_all("tr", recursive=False)

    print(f"Registered-project table rows found: {len(rows)}")

    for row in rows:
        cells = row.find_all("td", recursive=False)

        if not cells:
            continue

        def cell_text(header):
            index = header_map.get(header.lower())

            if index is None:
                return ""

            if index >= len(cells):
                return ""

            return clean_text(
                cells[index].get_text(" ", strip=True)
            )

        district = cell_text("Project District").upper()

        if district not in TARGET_DISTRICTS:
            continue

        webpage_index = header_map.get("view webpage")

        if webpage_index is None or webpage_index >= len(cells):
            continue

        link = cells[webpage_index].find("a", href=True)

        if not link:
            continue

        webpage_url = urljoin(BASE_URL, link["href"])
        project_id = extract_project_id(webpage_url)

        if not project_id:
            continue

        project = {
            "project_id": project_id,
            "registration_number": cell_text(
                "Registration Certificate Number"
            ),
            "project_name": cell_text("Project Name"),
            "promoter": cell_text("Promoter"),
            "project_location": cell_text("Project Location"),
            "project_district": district,
            "project_url": webpage_url,
        }

        projects.append(project)

    # Deduplicate by project ID
    unique = {}

    for project in projects:
        unique[project["project_id"]] = project

    return list(unique.values())


def extract_label_value(soup, label_pattern):
    """
    Find a table row where one cell/label contains label_pattern,
    then return the value from the row.

    Example:

      <tr>
        <td><label>Phone(Mobile)</label></td>
        <td><b>9435558183</b></td>
      </tr>
    """

    pattern = re.compile(
        label_pattern,
        flags=re.IGNORECASE,
    )

    for row in soup.find_all("tr"):
        text = clean_text(row.get_text(" ", strip=True))

        if not pattern.search(text):
            continue

        cells = row.find_all(["td", "th"])

        if len(cells) >= 2:
            value = clean_text(
                cells[-1].get_text(" ", strip=True)
            )

            if value:
                return value

    return ""


def extract_mobile(soup):
    """
    Extract only the publicly shared promoter mobile number.

    Prefer the explicit:
        Phone(Mobile)

    field and remove the surrounding public-sharing text.
    """

    for row in soup.find_all("tr"):
        row_text = clean_text(
            row.get_text(" ", strip=True)
        )

        if not re.search(
            r"Phone\s*\(\s*Mobile\s*\)",
            row_text,
            re.IGNORECASE,
        ):
            continue

        # Indian mobile number, allowing optional spaces/hyphens.
        matches = re.findall(
            r"(?<!\d)(?:\+91[\s-]*)?[6-9]\d{9}(?!\d)",
            row_text,
        )

        if matches:
            number = matches[0]

            number = re.sub(
                r"\D",
                "",
                number,
            )

            if number.startswith("91") and len(number) == 12:
                number = number[2:]

            if len(number) == 10:
                return number

    return ""


def extract_email(soup):
    """
    Extract email from the explicit Email field.
    """

    for row in soup.find_all("tr"):
        row_text = clean_text(
            row.get_text(" ", strip=True)
        )

        if not re.search(
            r"Email\s*(ID)?",
            row_text,
            re.IGNORECASE,
        ):
            continue

        matches = re.findall(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            row_text,
        )

        if matches:
            return matches[0]

    return ""


def extract_completion_date(soup):
    """
    Extract:

        ii) Likely date of completing the project

    This is the field we use to determine whether the project
    is expected to finish in 2029.

    We deliberately do NOT use the registered-project table's
    Expiry Date.
    """

    pattern = re.compile(
        r"likely\s+date\s+of\s+completing\s+the\s+project",
        flags=re.IGNORECASE,
    )

    for row in soup.find_all("tr"):
        row_text = clean_text(
            row.get_text(" ", strip=True)
        )

        if not pattern.search(row_text):
            continue

        cells = row.find_all(["td", "th"])

        # Usually:
        # [label, blank, value]
        if len(cells) >= 2:
            # Search the row from right to left for a date.
            for cell in reversed(cells):
                text = clean_text(
                    cell.get_text(" ", strip=True)
                )

                match = re.search(
                    r"\b(\d{2}-\d{2}-\d{4})\b",
                    text,
                )

                if match:
                    return match.group(1)

        # Fallback against complete row text.
        match = re.search(
            r"\b(\d{2}-\d{2}-\d{4})\b",
            row_text,
        )

        if match:
            return match.group(1)

    return ""


def extract_project_details(html):
    soup = BeautifulSoup(html, "html.parser")

    completion_date = extract_completion_date(soup)

    mobile = extract_mobile(soup)

    email = extract_email(soup)

    return {
        "completion_date": completion_date,
        "mobile": mobile,
        "email": email,
    }


def date_is_target_year(date_string):
    if not date_string:
        return False

    try:
        date = datetime.strptime(
            date_string,
            "%d-%m-%Y",
        )

        return date.year == TARGET_YEAR

    except ValueError:
        return False


def write_csv(projects):
    if not projects:
        return

    fieldnames = [
        "project_id",
        "registration_number",
        "project_name",
        "promoter",
        "project_location",
        "project_district",
        "completion_date",
        "mobile",
        "email",
        "project_url",
    ]

    with CSV_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()
        writer.writerows(projects)


def main():
    print("=" * 70)
    print("ASSAM RERA — KAMRUP / KAMRUP METRO — 2029 PROJECT CRAWLER")
    print("=" * 70)

    session = requests.Session()

    # ------------------------------------------------------------
    # STEP 1
    # ------------------------------------------------------------

    print("\n[1/3] Downloading registered-projects page...")

    response = get(
        session,
        REGISTERED_PROJECTS_URL,
    )

    if response is None:
        raise RuntimeError(
            "Unable to download registered-projects page."
        )

    print(
        f"Downloaded {len(response.content):,} bytes "
        f"(HTTP {response.status_code})"
    )

    # ------------------------------------------------------------
    # STEP 2
    # ------------------------------------------------------------

    print("\n[2/3] Extracting KAMRUP / KAMRUP METRO projects...")

    projects = parse_registered_projects(
        response.text
    )

    print(
        f"Target projects discovered: {len(projects)}"
    )

    if not projects:
        raise RuntimeError(
            "No KAMRUP/KAMRUP METRO projects were found."
        )

    print("\nFirst 10 discovered projects:")

    for project in projects[:10]:
        print(
            f"  {project['project_id']} | "
            f"{project['project_name']} | "
            f"{project['project_district']}"
        )

    # ------------------------------------------------------------
    # STEP 3
    # ------------------------------------------------------------

    print(
        "\n[3/3] Fetching project pages and checking "
        "LIKELY COMPLETION DATE..."
    )

    print(
        "Only projects whose Form-A completion year is 2029 "
        "will be retained."
    )

    target_projects = []

    total = len(projects)

    for index, project in enumerate(
        projects,
        start=1,
    ):
        project_id = project["project_id"]

        print(
            f"\n[{index}/{total}] "
            f"{project_id} | "
            f"{project['project_name']}"
        )

        detail_response = get(
            session,
            project["project_url"],
        )

        if detail_response is None:
            print("  ERROR: could not download project page")
            continue

        details = extract_project_details(
            detail_response.text
        )

        completion_date = details[
            "completion_date"
        ]

        print(
            f"  Completion: "
            f"{completion_date or 'NOT FOUND'}"
        )

        if not date_is_target_year(
            completion_date
        ):
            print("  -> SKIP")
            time.sleep(REQUEST_DELAY)
            continue

        project.update(details)

        target_projects.append(project)

        print("  -> MATCH 2029")

        print(
            f"  Mobile: "
            f"{project['mobile'] or 'NOT FOUND'}"
        )

        print(
            f"  Email: "
            f"{project['email'] or 'NOT FOUND'}"
        )

        time.sleep(REQUEST_DELAY)

    # ------------------------------------------------------------
    # OUTPUT
    # ------------------------------------------------------------

    print("\n" + "=" * 70)
    print("CRAWL COMPLETE")
    print("=" * 70)

    print(
        f"Projects discovered: {len(projects)}"
    )

    print(
        f"2029 projects:       {len(target_projects)}"
    )

    write_csv(target_projects)

    print(
        f"\nCSV written to:\n"
        f"  {CSV_FILE.resolve()}"
    )

    print("\n2029 PROJECTS:")

    for project in target_projects:
        print(
            f"\n  {project['project_id']}"
            f"\n    {project['project_name']}"
            f"\n    {project['project_district']}"
            f"\n    Completion: {project['completion_date']}"
            f"\n    Promoter: {project['promoter']}"
            f"\n    Mobile: {project['mobile']}"
            f"\n    Email: {project['email']}"
        )


if __name__ == "__main__":
    main()