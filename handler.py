import base64
import binascii
import io
import logging
import os
import zipfile as zf
import xml.etree.ElementTree as ET

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("fusion_ess_extractor")

app = FastAPI(title="Fusion ESS Report Extractor")

class ReportRequest(BaseModel):
    document_content: str = Field(
        ...,
        description="Base64-encoded ZIP (DocumentContent) for a single ESS report.",
    )


class CompareRequest(BaseModel):
    trial_balance_document_content: str = Field(
        ..., description="Base64-encoded ZIP for the Trial Balance report."
    )
    aging_document_content: str = Field(
        ..., description="Base64-encoded ZIP for the Supplier Balance Aging report."
    )


def _decode_base64(document_content: str) -> bytes:
    logger.info("Decoding base64 document content (%d chars)", len(document_content))
    try:
        decoded = base64.b64decode(document_content, validate=True)
    except (binascii.Error, ValueError) as exc:
        logger.error("Failed to decode base64 document content: %s", exc)
        raise HTTPException(status_code=400, detail=f"Invalid base64 document_content: {exc}")

    logger.info("Decoded document content: %d bytes", len(decoded))
    return decoded


def _extract_xml_from_zip(zip_bytes: bytes) -> bytes:
    logger.info("Extracting XML from ZIP archive (%d bytes)", len(zip_bytes))
    try:
        with zf.ZipFile(io.BytesIO(zip_bytes), "r") as zip_file:
            namelist = zip_file.namelist()
            logger.debug("ZIP contains files: %s", namelist)

            xml_file = next(
                (name for name in namelist if name.lower().endswith(".xml")),
                None,
            )
            if xml_file is None:
                logger.error("No XML file found inside ZIP. Contents: %s", namelist)
                raise HTTPException(status_code=422, detail="No XML file found inside ZIP")

            logger.info("Found XML file inside ZIP: %s", xml_file)
            return zip_file.read(xml_file)
    except zf.BadZipFile as exc:
        logger.error("document_content did not decode to a valid ZIP: %s", exc)
        raise HTTPException(status_code=400, detail=f"document_content is not a valid ZIP: {exc}")


def _parse_report(xml_bytes: bytes) -> tuple[str, list[dict]]:
    logger.info("Parsing report XML (%d bytes)", len(xml_bytes))
    xml_text = xml_bytes.decode("utf-8", errors="replace")
    root = ET.fromstring(xml_bytes)

    if "GLTRBAL" in xml_text:
        report_name = "Trial Balance Report"
        logger.info("Detected report type: %s (GLTRBAL)", report_name)
        g_details = root.findall(".//G_DETAIL")
        if not g_details:
            logger.error("Trial Balance XML had no G_DETAIL rows")
            raise HTTPException(status_code=422, detail="Trial Balance XML had no G_DETAIL rows")

        results = []
        for detail in g_details:
            account_id = detail.find('.//ACCT')
            begin_balance = detail.find('.//BEGIN_BALANCE')
            total_debits = detail.find('.//TOTAL_DR')
            total_credits = detail.find('.//TOTAL_CR')
            end_balance = detail.find('.//END_BALANCE')
            results.append(
                    {
                    "CodeCombination": account_id.text if account_id is not None else None,
                    "BeginBalance": begin_balance.text if begin_balance is not None else None,
                    "TotalDebits": total_debits.text if total_debits is not None else None,
                    "TotalCredits": total_credits.text if total_credits is not None else None,
                    "EndBalance": end_balance.text if end_balance is not None else None
                }
            )
        logger.info("Parsed %d %s rows", len(results), report_name)
        return report_name, results

    elif "DATA_DS" in xml_text:
        report_name = "Supplier Balance Aging Report"
        logger.info("Detected report type: %s (DATA_DS)", report_name)
        accounts_summary = root.findall(".//ACCOUNT_SUMMARY")
        if not accounts_summary:
            logger.error("Supplier Balance Aging XML had no ACCOUNT_SUMMARY rows")
            raise HTTPException(status_code=422, detail="Supplier Balance Aging XML had no ACCOUNT_SUMMARY rows")

        results = []
        for summary in accounts_summary:
            account_id = summary.find(".//ACCOUNT")
            amount = summary.find(".//AMOUNT")
            results.append(
                {
                    "CodeCombination": account_id.text if account_id is not None else None,
                    "Amount": amount.text if amount is not None else None,
                }
            )
        logger.info("Parsed %d %s rows", len(results), report_name)
        return report_name, results

    else:
        logger.error("Unknown report type. XML does not contain GLTRBAL or DATA_DS.")
        raise HTTPException(
            status_code=422,
            detail="Unknown report type. XML does not contain GLTRBAL or DATA_DS.",
        )


def _get_report_records(document_content: str) -> tuple[str, list[dict]]:
    zip_bytes = _decode_base64(document_content)
    xml_bytes = _extract_xml_from_zip(zip_bytes)
    return _parse_report(xml_bytes)


def _to_number(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None

@app.post("/report")
def get_report(payload: ReportRequest):
    logger.info("Received request for /report (base64 document_content)")
    report_name, records = _get_report_records(payload.document_content)
    logger.info("Returning %d records for report_name=%s", len(records), report_name)
    return JSONResponse(
        content={"report_name": report_name, "count": len(records), "data": records}
    )