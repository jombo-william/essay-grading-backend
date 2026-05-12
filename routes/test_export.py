from fastapi import APIRouter
from fastapi.responses import Response
import json

router = APIRouter(prefix="/test", tags=["Test"])

@router.get("/export-quiz/{quiz_id}")
def test_export(quiz_id: int):
    """Test export without auth"""
    # Simple XML for testing
    xml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<quiz>
  <question type="multichoice">
    <name>
      <text>Test Question 1</text>
    </name>
    <questiontext format="html">
      <text><![CDATA[What is 2+2?]]></text>
    </questiontext>
    <defaultgrade>1</defaultgrade>
    <single>true</single>
    <shuffleanswers>true</shuffleanswers>
    <answer fraction="100">
      <text>4</text>
    </answer>
    <answer fraction="0">
      <text>3</text>
    </answer>
  </question>
</quiz>'''
    
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename=test_quiz_{quiz_id}.xml"}
    )
