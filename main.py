import os
import base64
import zipfile as zf
import io
import xml.etree.ElementTree as ET

import requests

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv


# ============================================================
# Load environment variables
# ============================================================

load_dotenv()


# ============================================================
# FastAPI application
# ============================================================

app = FastAPI(
    title="Oracle Fusion Report Processing API",
    version="1.0.0"
)


# ============================================================
# Request model
# ============================================================

class ReportRequest(BaseModel):
    request_id: int


# ============================================================
# Configuration
# ============================================================

BASE_URL = "https://iaaley-test.fa.ocs.oraclecloud.com"

FUSION_USERNAME = os.getenv("FUSION_USERNAME")
FUSION_PASSWORD = os.getenv("FUSION_PASSWORD")


# ============================================================
# API endpoint
# ============================================================

@app.post("/process-report")
def process_report(request: ReportRequest):

    REQUEST_ID = request.request_id

    # --------------------------------------------------------
    # Validate credentials
    # --------------------------------------------------------

    if not FUSION_USERNAME or not FUSION_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="Fusion API credentials are not configured."
        )

    # --------------------------------------------------------
    # Build Oracle Fusion URL
    # --------------------------------------------------------

    URL = (
        f"{BASE_URL}/fscmRestApi/resources/11.13.18.05/"
        f"erpintegrations"
        f"?finder=ESSJobExecutionDetailsRF;"
        f"requestId={REQUEST_ID},fileType=ALL"
    )

    AUTH = (
        FUSION_USERNAME,
        FUSION_PASSWORD
    )

    # --------------------------------------------------------
    # Call Oracle Fusion REST API
    # --------------------------------------------------------

    try:

        response = requests.get(
            URL,
            auth=AUTH,
            headers={
                "Accept": "application/json"
            },
            timeout=120
        )

    except requests.RequestException as e:

        raise HTTPException(
            status_code=502,
            detail=f"Failed to connect to Fusion API: {str(e)}"
        )

    # --------------------------------------------------------
    # Check Fusion response
    # --------------------------------------------------------

    if response.status_code != 200:

        raise HTTPException(
            status_code=response.status_code,
            detail={
                "message": "Fusion API returned an error",
                "fusion_status_code": response.status_code,
                "fusion_response": response.text
            }
        )

    try:

        response_data = response.json()

    except ValueError:

        raise HTTPException(
            status_code=502,
            detail="Fusion API did not return valid JSON."
        )

    # --------------------------------------------------------
    # Get items
    # --------------------------------------------------------

    items = response_data.get("items", [])

    if not items:

        raise HTTPException(
            status_code=404,
            detail="No items found in Fusion API response."
        )

    # --------------------------------------------------------
    # Get DocumentContent
    # --------------------------------------------------------

    document_content = items[0].get("DocumentContent")

    if not document_content:

        raise HTTPException(
            status_code=404,
            detail="DocumentContent is empty."
        )

    # --------------------------------------------------------
    # Base64 decode
    # --------------------------------------------------------

    try:

        decoded_bytes = base64.b64decode(
            document_content,
            validate=True
        )

    except Exception as e:

        raise HTTPException(
            status_code=400,
            detail=f"Failed to decode DocumentContent: {str(e)}"
        )

    # --------------------------------------------------------
    # Open ZIP
    # --------------------------------------------------------

    try:

        with zf.ZipFile(
            io.BytesIO(decoded_bytes),
            "r"
        ) as zip_file:

            file_names = zip_file.namelist()

            # --------------------------------------------
            # Find XML file
            # --------------------------------------------

            xml_file = None

            for file_name in file_names:

                if file_name.lower().endswith(".xml"):

                    xml_file = file_name
                    break

            if xml_file is None:

                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "No XML file found inside ZIP.",
                        "files_in_zip": file_names
                    }
                )

            # --------------------------------------------
            # Read XML
            # --------------------------------------------

            xml_bytes = zip_file.read(xml_file)

            xml_text = xml_bytes.decode(
                "utf-8",
                errors="replace"
            )

    except HTTPException:
        raise

    except zf.BadZipFile:

        raise HTTPException(
            status_code=400,
            detail="DocumentContent is not a valid ZIP file."
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Failed to process ZIP file: {str(e)}"
        )

    # --------------------------------------------------------
    # Parse XML
    # --------------------------------------------------------

    try:

        root = ET.fromstring(xml_bytes)

    except ET.ParseError as e:

        raise HTTPException(
            status_code=400,
            detail=f"Invalid XML content: {str(e)}"
        )

    # ========================================================
    # TRIAL BALANCE
    # ========================================================

    if "GLTRBAL" in xml_text:

        report_type = "TRIAL_BALANCE"

        g_details = root.findall(".//G_DETAIL")

        data = []

        for detail in g_details:

            account_id = detail.find(".//ACCT")
            begin_balance = detail.find(".//BEGIN_BALANCE")
            total_debits = detail.find(".//TOTAL_DR")
            total_credits = detail.find(".//TOTAL_CR")
            end_balance = detail.find(".//END_BALANCE")

            data.append(
                {
                    "account_id":
                        account_id.text
                        if account_id is not None
                        else None,

                    "begin_balance":
                        begin_balance.text
                        if begin_balance is not None
                        else None,

                    "total_debits":
                        total_debits.text
                        if total_debits is not None
                        else None,

                    "total_credits":
                        total_credits.text
                        if total_credits is not None
                        else None,

                    "end_balance":
                        end_balance.text
                        if end_balance is not None
                        else None
                }
            )

    # ========================================================
    # SUPPLIER BALANCE AGING
    # ========================================================

    elif "DATA_DS" in xml_text:

        report_type = "SUPPLIER_BALANCE_AGING"

        accounts_summary = root.findall(
            ".//ACCOUNT_SUMMARY"
        )

        data = []

        for summary in accounts_summary:

            account_id = summary.find(".//ACCOUNT")
            amount = summary.find(".//AMOUNT")

            data.append(
                {
                    "account_id":
                        account_id.text
                        if account_id is not None
                        else None,

                    "amount":
                        amount.text
                        if amount is not None
                        else None
                }
            )

    # ========================================================
    # UNKNOWN REPORT
    # ========================================================

    else:

        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown report type. "
                "XML does not contain GLTRBAL or DATA_DS."
            )
        )

    # ========================================================
    # RETURN API RESPONSE
    # ========================================================

    return {
        "status": "SUCCESS",
        "request_id": REQUEST_ID,
        "report_type": report_type,
        "xml_file": xml_file,
        "record_count": len(data),
        "data": data
    }