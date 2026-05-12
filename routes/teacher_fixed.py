@router.get("/classes")
def get_teacher_classes(
    ctx: dict = Depends(require_teacher),
    db: Session = Depends(get_db)
):
    user = ctx["user"]
    
    classes = db.query(Class).join(TeacherClass).filter(
        TeacherClass.teacher_id == user.id
    ).all()
    
    # Convert to dict for JSON response - only use fields that exist
    result = []
    for c in classes:
        class_dict = {
            "id": c.id,
            "name": c.name,
        }
        # Add optional fields if they exist
        if hasattr(c, "section") and c.section:
            class_dict["section"] = c.section
        if hasattr(c, "subject") and c.subject:
            class_dict["subject"] = c.subject
        if hasattr(c, "description") and c.description:
            class_dict["description"] = c.description
            
        result.append(class_dict)
    
    return {"success": True, "classes": result}
