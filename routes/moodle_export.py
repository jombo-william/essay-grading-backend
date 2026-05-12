from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database import get_db
from auth_utils import require_teacher
import models
import json
import xml.etree.ElementTree as ET
from xml.dom import minidom

router = APIRouter(prefix="/moodle", tags=["Moodle Export"])

@router.get("/export-quiz/{quiz_id}")
def export_quiz_to_moodle_xml(
    quiz_id: int,
    ctx: dict = Depends(require_teacher),
    db: Session = Depends(get_db)
):
    """Export quiz in Moodle XML format for manual import"""
    user = ctx["user"]
    
    quiz = db.query(models.Quiz).filter(
        models.Quiz.id == quiz_id,
        models.Quiz.teacher_id == user.id
    ).first()
    
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    
    # Parse questions
    questions = quiz.questions if isinstance(quiz.questions, list) else json.loads(quiz.questions)
    
    # Create root element
    quiz_element = ET.Element("quiz")
    
    for idx, question in enumerate(questions):
        question_type = question.get('question_type', 'multichoice')
        
        # Map question type to Moodle format
        moodle_type = {
            'multiple_choice': 'multichoice',
            'true_false': 'truefalse',
            'short_answer': 'shortanswer',
            'essay': 'essay'
        }.get(question_type, 'multichoice')
        
        # Create question element
        q_element = ET.SubElement(quiz_element, "question", type=moodle_type)
        
        # Add name
        name = ET.SubElement(q_element, "name")
        name_text = ET.SubElement(name, "text")
        name_text.text = f"Q{idx+1}: {question.get('question_text', '')[:50]}"
        
        # Add question text
        questiontext = ET.SubElement(q_element, "questiontext", format="html")
        q_text = ET.SubElement(questiontext, "text")
        q_text.text = f"<![CDATA[{question.get('question_text', '')}]]>"
        
        # Add default grade
        defaultgrade = ET.SubElement(q_element, "defaultgrade")
        defaultgrade.text = str(question.get('points', 1))
        
        # Add penalty
        penalty = ET.SubElement(q_element, "penalty")
        penalty.text = "0.3333333"
        
        # Add hidden
        hidden = ET.SubElement(q_element, "hidden")
        hidden.text = "0"
        
        # Handle different question types
        if question_type == 'multiple_choice':
            single = ET.SubElement(q_element, "single")
            single.text = "true"
            shuffleanswers = ET.SubElement(q_element, "shuffleanswers")
            shuffleanswers.text = "true"
            
            for opt in question.get('options', []):
                answer = ET.SubElement(q_element, "answer", fraction="100" if opt.get('is_correct') else "0")
                answer_text = ET.SubElement(answer, "text")
                answer_text.text = opt.get('text', '')
                
                # Add feedback (empty)
                feedback = ET.SubElement(answer, "feedback")
                feedback_text = ET.SubElement(feedback, "text")
                feedback_text.text = ""
        
        elif question_type == 'true_false':
            # Correct answer
            correct_answer = question.get('correct_answer', 'True')
            answer_true = ET.SubElement(q_element, "answer", fraction="100" if correct_answer == "True" else "0")
            answer_true_text = ET.SubElement(answer_true, "text")
            answer_true_text.text = "True"
            
            answer_false = ET.SubElement(q_element, "answer", fraction="100" if correct_answer == "False" else "0")
            answer_false_text = ET.SubElement(answer_false, "text")
            answer_false_text.text = "False"
        
        elif question_type == 'short_answer':
            # Short answer question
            usecase = ET.SubElement(q_element, "usecase")
            usecase.text = "0"
            
            if question.get('correct_answer'):
                answer = ET.SubElement(q_element, "answer", fraction="100")
                answer_text = ET.SubElement(answer, "text")
                answer_text.text = question.get('correct_answer', '')
        
        elif question_type == 'essay':
            # Essay question format
            responseformat = ET.SubElement(q_element, "responseformat")
            responseformat.text = "editor"
            responserequired = ET.SubElement(q_element, "responserequired")
            responserequired.text = "1"
            responsefieldlines = ET.SubElement(q_element, "responsefieldlines")
            responsefieldlines.text = "15"
            attachments = ET.SubElement(q_element, "attachments")
            attachments.text = "0"
            attachmentsrequired = ET.SubElement(q_element, "attachmentsrequired")
            attachmentsrequired.text = "0"
    
    # Convert to pretty XML
    rough_string = ET.tostring(quiz_element, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    xml_content = reparsed.toprettyxml(indent="  ")
    
    # Add XML declaration
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_content
    
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename=quiz_{quiz.id}_{quiz.title}.xml"}
    )
