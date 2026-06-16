"""
Patient-friendly prescription instruction formatting.
Converts Latin abbreviations to plain English and Telugu.
"""

FREQUENCY_DISPLAY = {
    "OD": "Once daily",
    "BD": "Twice daily",
    "TDS": "Three times daily",
    "QID": "Four times daily",
    "SOS": "When needed",
}

FREQUENCY_TELUGU = {
    "OD": "రోజుకి ఒకసారి",
    "BD": "రోజుకి రెండుసార్లు",
    "TDS": "రోజుకి మూడుసార్లు",
    "QID": "రోజుకి నాలుగుసార్లు",
    "SOS": "అవసరమైనప్పుడు",
}

INSTRUCTION_DISPLAY = {
    "after food": "after food",
    "before food": "before food",
    "at bedtime": "at bedtime",
    "at night": "at night",
    "empty stomach": "on empty stomach",
    "with water": "with water",
    "after breakfast": "after breakfast",
    "after lunch": "after lunch",
    "after dinner": "after dinner"
}

INSTRUCTION_TELUGU = {
    "after food": "తిన్న తర్వాత",
    "before food": "తినడానికి ముందు",
    "at bedtime": "పడుకునే ముందు",
    "at night": "రాత్రి",
    "empty stomach": "ఖాళీ కడుపుతో",
    "with water": "నీళ్ళతో",
    "after breakfast": "అల్పాహారం తర్వాత",
    "after lunch": "భోజనం తర్వాత",
    "after dinner": "రాత్రి భోజనం తర్వాత"
}

TIME_OF_DAY = {
    "OD": "Morning",
    "BD": "Morning and Night",
    "TDS": "Morning, Afternoon and Night",
    "QID": "Morning, Afternoon, Evening and Night",
    "SOS": "As required",
}

TIME_OF_DAY_TELUGU = {
    "OD": "ఉదయం",
    "BD": "ఉదయం మరియు రాత్రి",
    "TDS": "ఉదయం, మధ్యాహ్నం మరియు రాత్రి",
    "QID": "ఉదయం, మధ్యాహ్నం, సాయంత్రం మరియు రాత్రి",
    "SOS": "అవసరమైనప్పుడు",
}


def format_patient_instruction(drug_name, dosage, frequency, duration, instruction):
    """
    Format a prescription instruction for patient display in English and Telugu.
    
    Args:
        drug_name: Name of the drug
        dosage: Dosage (e.g., "500mg")
        frequency: Frequency code (e.g., "OD", "BD", "TDS", "QID", "SOS")
        duration: Duration (e.g., "5 days", "14 days")
        instruction: Instruction (e.g., "after food", "before food")
    
    Returns:
        Dictionary with 'english' and 'telugu' keys containing formatted instructions
    """
    freq_english = FREQUENCY_DISPLAY.get(frequency, frequency)
    freq_telugu = FREQUENCY_TELUGU.get(frequency, "")
    time_english = TIME_OF_DAY.get(frequency, "")
    time_telugu = TIME_OF_DAY_TELUGU.get(frequency, "")
    
    instr_english = INSTRUCTION_DISPLAY.get(
        instruction.lower() if instruction else "", instruction)
    instr_telugu = INSTRUCTION_TELUGU.get(
        instruction.lower() if instruction else "", "")
    
    if frequency == "SOS":
        english_line = f"{freq_english} (SOS)"
        telugu_line = f"{freq_telugu} (SOS)"
    else:
        english_line = f"{freq_english} ({time_english})"
        telugu_line = f"{freq_telugu} ({time_telugu})"
    if instr_english:
        english_line += f" — {instr_english}"
    if duration:
        english_line += f" — {duration}"

    if instr_telugu:
        telugu_line += f" — {instr_telugu}"
    
    return {
        "english": english_line,
        "telugu": telugu_line
    }
