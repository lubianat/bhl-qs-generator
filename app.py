from flask import Flask, request, render_template, jsonify, flash
from markupsafe import Markup
from wdcuration import lookup_id, render_qs_url
import requests
import re
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = "your-secret-key"  # Required for flash messages


# Try to load variables from a .env file if the package is available.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    print(
        "Warning: python-dotenv is not installed. Using system environment variables."
    )

# Get the API key from the environment (Railway injects shared variables automatically)
BHL_API_KEY = os.getenv("BHL_API_KEY")
if not BHL_API_KEY:
    raise EnvironmentError(
        "BHL_API_KEY is not defined. Please set it in your environment variables."
    )


BHL_TITLE_METADATA_URL = (
    "https://www.biodiversitylibrary.org/api3"
    "?op=GetTitleMetadata&id={title_id}&format=json&items=true&apikey=" + BHL_API_KEY
)


def parse_bhl_title_id(input_str):
    match = re.search(r"bhl\.title\.(\d+)", input_str)
    if match:
        return match.group(1)
    if input_str.isdigit():
        return input_str
    match = re.search(r"(\d+)$", input_str)
    return match.group(1) if match else None


def get_bhl_title_metadata(title_id):
    """Fetch metadata and gracefully return None if no result is found."""
    response = requests.get(BHL_TITLE_METADATA_URL.format(title_id=title_id))
    if response.ok:
        data = response.json()
        if (
            data.get("Status") == "ok"
            and data.get("Result")
            and len(data["Result"]) > 0
        ):
            return data["Result"][0]
    return None


def generate_title_quickstatements(title_data):
    """Generate QuickStatements commands for creating a Wikidata page for the title."""
    commands = []
    full_title = title_data.get("FullTitle", "Unknown Title")
    bhl_title_id = title_data.get("TitleID", "")
    title_url = f"https://www.biodiversitylibrary.org/bibliography/{bhl_title_id}"

    commands.append("CREATE")
    commands.append(f'LAST|Lmul|"{full_title}"')
    commands.append(f'LAST|Den|"title in the Biodiversity Heritage Library collection"')
    commands.append("LAST|P31|Q47461344")
    commands.append(f'LAST|P4327|"{bhl_title_id}"')
    commands.append(f'LAST|P953|"{title_url}"')

    for identifier in title_data.get("Identifiers", []):
        if identifier["IdentifierName"] == "DOI":
            commands.append(
                f'LAST|P356|"{identifier["IdentifierValue"].upper()}"|S854|"https://www.biodiversitylibrary.org/bibliography/{bhl_title_id}"'
            )
        elif identifier["IdentifierName"] == "ISSN":
            commands.append(
                f'LAST|P236|"{identifier["IdentifierValue"].upper()}"|S854|"https://www.biodiversitylibrary.org/bibliography/{bhl_title_id}"'
            )
        elif identifier["IdentifierName"] == "OCLC":
            commands.append(
                f'LAST|P243|"{identifier["IdentifierValue"].upper()}"|S854|"https://www.biodiversitylibrary.org/bibliography/{bhl_title_id}"'
            )
    for author in title_data.get("Authors", []):
        author_qid = lookup_id(id=author["AuthorID"], property="P4081")
        author_name = author["Name"]
        if author_qid:
            commands.append(
                f'LAST|P50|{author_qid}|S854|"https://www.biodiversitylibrary.org/bibliography/{bhl_title_id}"|S1932|"{author_name}"'
            )
        else:
            print(f"QID NOT FOUND FOR author: {author}")
            commands.append(
                f'LAST|P2093|"{author_name}"|S854|"https://www.biodiversitylibrary.org/bibliography/{bhl_title_id}"'
            )
    return "\n".join(commands)


def check_existing_wikidata_item(bhl_title_id):
    """
    Checks Wikidata to see if an item with the given BHL Title ID (property P4327) exists.
    Returns a tuple (qid, label) if found, or (None, None) otherwise.
    """
    query = """
    SELECT ?item ?itemLabel WHERE {
      ?item wdt:P4327 "%s" .
      SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],mul,en". }
    }
    """ % (
        bhl_title_id
    )
    url = "https://query.wikidata.org/sparql"
    headers = {"Accept": "application/sparql-results+json"}
    params = {"query": query}
    response = requests.get(url, headers=headers, params=params)
    if response.ok:
        data = response.json()
        results = data.get("results", {}).get("bindings", [])
        if results:
            item_url = results[0]["item"]["value"]
            qid = item_url.split("/")[-1]
            label = results[0].get("itemLabel", {}).get("value", "")
            return qid, label
    return None, None


@app.route("/", methods=["GET"])
def index():
    bhl_input = request.args.get("bhl", "").strip()
    context = {}
    if bhl_input:
        title_id = parse_bhl_title_id(bhl_input)
        context["title_id"] = title_id or "Invalid input"

        # Check for existing Wikidata item.
        existing_qid, existing_label = check_existing_wikidata_item(title_id)
        if existing_qid:
            error_msg = f'Item already exists: <a href="https://www.wikidata.org/wiki/{existing_qid}" target="_blank">{existing_label} (Q{existing_qid})</a>. Please check the item on Wikidata.'
            context["error"] = Markup(error_msg)
            context["existing_item"] = {
                "qid": existing_qid,
                "label": existing_label,
                "url": f"https://www.wikidata.org/wiki/{existing_qid}",
            }
            return render_template("index.html", **context)

        title_data = get_bhl_title_metadata(title_id)
        if title_data:
            quickstatements = generate_title_quickstatements(title_data)
            qs_url = render_qs_url(quickstatements)
            context.update(
                title_data=title_data,
                quickstatements=quickstatements,
                qs_url=qs_url,
            )
        else:
            error_msg = "No metadata found for this BHL Title ID. Please verify the ID and try again."
            context["error"] = error_msg

    return render_template("index.html", **context)


# API endpoint remains the same.
@app.route("/api/quickstatements", methods=["GET"])
def api_quickstatements():
    raw_id = request.args.get("id", "").strip()
    if not raw_id:
        return jsonify({"error": "No id provided."}), 400

    title_id = parse_bhl_title_id(raw_id)
    if not title_id:
        return jsonify({"error": "Invalid id provided."}), 400

    existing_qid, existing_label = check_existing_wikidata_item(title_id)
    if existing_qid:
        return (
            jsonify(
                {"error": f"Item already exists: {existing_label} (Q{existing_qid})."}
            ),
            400,
        )

    title_data = get_bhl_title_metadata(title_id)
    if not title_data:
        return jsonify({"error": "No metadata found for this BHL Title ID."}), 404

    quickstatements = generate_title_quickstatements(title_data)
    return jsonify({"quickstatements": quickstatements})


if __name__ == "__main__":
    # Use the port that Railway provides via the PORT environment variable.
    port = int(os.environ.get("PORT", 5000))
    # Listen on all network interfaces.
    app.run(host="0.0.0.0", port=port, debug=False)
