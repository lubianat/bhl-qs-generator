from flask import Flask, request, render_template, jsonify
from login import BHL_API_KEY
from wdcuration import lookup_id, render_qs_url
import requests
import re

app = Flask(__name__)

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
    response = requests.get(BHL_TITLE_METADATA_URL.format(title_id=title_id))
    if response.ok and response.json().get("Status") == "ok":
        return response.json()["Result"][0]
    return None


def generate_title_quickstatements(title_data):
    """Generate QuickStatements commands for creating a Wikidata page for the title."""
    commands = []
    full_title = title_data.get("FullTitle", "Unknown Title")
    bhl_title_id = title_data.get("TitleID", "")
    title_url = f"https://www.biodiversitylibrary.org/bibliography/{bhl_title_id}"

    # Create the new Wikidata item for the title
    commands.append("CREATE")
    commands.append(f'LAST|Lmul|"{full_title}"')
    commands.append(f'LAST|Den|"title in the Biodiversity Heritage Library collection"')

    # Set instance of bibliographic work (Q47461344); adjust if needed
    commands.append("LAST|P31|Q47461344")
    # Add the BHL Title ID (property P4327)
    commands.append(f'LAST|P4327|"{bhl_title_id}"')
    # Add the reference URL
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
    # Add authors as creators if available
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
    Checks Wikidata to see if an item with the given BHL Title ID (property P4327) already exists.
    Returns a tuple (qid, label) if an item is found, or (None, None) otherwise.
    """
    query = """
    SELECT ?item ?itemLabel WHERE {
      ?item wdt:P4327 "%s" .
      SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }
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
            item_url = results[0]["item"][
                "value"
            ]  # e.g., "http://www.wikidata.org/entity/Q123456"
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
        # Check for an existing Wikidata item with this BHL Title ID.
        existing_qid, existing_label = check_existing_wikidata_item(title_id)
        if existing_qid:
            context["existing_item"] = {
                "qid": existing_qid,
                "label": existing_label,
                "url": f"https://www.wikidata.org/wiki/{existing_qid}",
            }
            context["error"] = (
                f"Item already exists: {existing_label} (Q{existing_qid}). "
                f"Please check the item on Wikidata."
            )
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
    return render_template("index.html", **context)


# New API endpoint:
@app.route("/api/quickstatements", methods=["GET"])
def api_quickstatements():
    """API endpoint that receives a BHL Title ID and returns the generated QuickStatements string in JSON."""
    raw_id = request.args.get("id", "").strip()
    if not raw_id:
        return jsonify({"error": "No id provided."}), 400

    title_id = parse_bhl_title_id(raw_id)
    if not title_id:
        return jsonify({"error": "Invalid id provided."}), 400

    # Check if item already exists in Wikidata
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
    # You can return a plain JSON response with the string.
    return jsonify({"quickstatements": quickstatements})


if __name__ == "__main__":
    app.run(debug=False)
