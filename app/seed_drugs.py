"""
Seed script: inserts 100 most common Indian drugs into the drugs table.
Run with: python -m app.seed_drugs
"""
import asyncio
from app.database import AsyncSessionLocal, engine, Base
from app.models import Drug

COMMON_INDIAN_DRUGS = [
    # Cardiovascular
    ("Amlodipine", "Amlodipine Besylate", "Cardiovascular"),
    ("Atenolol", "Atenolol", "Cardiovascular"),
    ("Atorvastatin", "Atorvastatin Calcium", "Cardiovascular"),
    ("Rosuvastatin", "Rosuvastatin Calcium", "Cardiovascular"),
    ("Telmisartan", "Telmisartan", "Cardiovascular"),
    ("Losartan", "Losartan Potassium", "Cardiovascular"),
    ("Ramipril", "Ramipril", "Cardiovascular"),
    ("Enalapril", "Enalapril Maleate", "Cardiovascular"),
    ("Metoprolol", "Metoprolol Succinate", "Cardiovascular"),
    ("Aspirin", "Acetylsalicylic Acid", "Cardiovascular"),
    ("Clopidogrel", "Clopidogrel Bisulfate", "Cardiovascular"),
    ("Digoxin", "Digoxin", "Cardiovascular"),
    ("Furosemide", "Furosemide", "Cardiovascular"),
    ("Spironolactone", "Spironolactone", "Cardiovascular"),
    ("Nitroglycerin", "Glyceryl Trinitrate", "Cardiovascular"),
    # Diabetes
    ("Metformin", "Metformin Hydrochloride", "Diabetes"),
    ("Glimepiride", "Glimepiride", "Diabetes"),
    ("Glibenclamide", "Glibenclamide", "Diabetes"),
    ("Sitagliptin", "Sitagliptin Phosphate", "Diabetes"),
    ("Vildagliptin", "Vildagliptin", "Diabetes"),
    ("Dapagliflozin", "Dapagliflozin", "Diabetes"),
    ("Empagliflozin", "Empagliflozin", "Diabetes"),
    ("Insulin Glargine", "Insulin Glargine", "Diabetes"),
    ("Insulin Regular", "Human Insulin", "Diabetes"),
    # Antibiotics
    ("Amoxicillin", "Amoxicillin Trihydrate", "Antibiotic"),
    ("Amoxicillin-Clavulanate", "Amoxicillin + Clavulanic Acid", "Antibiotic"),
    ("Azithromycin", "Azithromycin Dihydrate", "Antibiotic"),
    ("Ciprofloxacin", "Ciprofloxacin Hydrochloride", "Antibiotic"),
    ("Levofloxacin", "Levofloxacin Hemihydrate", "Antibiotic"),
    ("Doxycycline", "Doxycycline Hyclate", "Antibiotic"),
    ("Cefixime", "Cefixime Trihydrate", "Antibiotic"),
    ("Cefpodoxime", "Cefpodoxime Proxetil", "Antibiotic"),
    ("Metronidazole", "Metronidazole", "Antibiotic"),
    ("Tinidazole", "Tinidazole", "Antibiotic"),
    ("Nitrofurantoin", "Nitrofurantoin", "Antibiotic"),
    ("Cotrimoxazole", "Sulfamethoxazole + Trimethoprim", "Antibiotic"),
    # Analgesics / Anti-inflammatory
    ("Paracetamol", "Acetaminophen", "Analgesic"),
    ("Ibuprofen", "Ibuprofen", "NSAID"),
    ("Diclofenac", "Diclofenac Sodium", "NSAID"),
    ("Aceclofenac", "Aceclofenac", "NSAID"),
    ("Naproxen", "Naproxen Sodium", "NSAID"),
    ("Etoricoxib", "Etoricoxib", "NSAID"),
    ("Tramadol", "Tramadol Hydrochloride", "Analgesic"),
    # Gastrointestinal
    ("Omeprazole", "Omeprazole", "GI"),
    ("Pantoprazole", "Pantoprazole Sodium", "GI"),
    ("Rabeprazole", "Rabeprazole Sodium", "GI"),
    ("Esomeprazole", "Esomeprazole Magnesium", "GI"),
    ("Ranitidine", "Ranitidine Hydrochloride", "GI"),
    ("Domperidone", "Domperidone", "GI"),
    ("Ondansetron", "Ondansetron Hydrochloride", "GI"),
    ("Metoclopramide", "Metoclopramide Hydrochloride", "GI"),
    ("Loperamide", "Loperamide Hydrochloride", "GI"),
    ("Lactulose", "Lactulose", "GI"),
    ("Bisacodyl", "Bisacodyl", "GI"),
    ("Sucralfate", "Sucralfate", "GI"),
    # Respiratory
    ("Salbutamol", "Salbutamol Sulfate", "Respiratory"),
    ("Levosalbutamol", "Levosalbutamol Sulfate", "Respiratory"),
    ("Budesonide", "Budesonide", "Respiratory"),
    ("Fluticasone", "Fluticasone Propionate", "Respiratory"),
    ("Montelukast", "Montelukast Sodium", "Respiratory"),
    ("Theophylline", "Theophylline", "Respiratory"),
    ("Ipratropium", "Ipratropium Bromide", "Respiratory"),
    ("Tiotropium", "Tiotropium Bromide", "Respiratory"),
    ("Dextromethorphan", "Dextromethorphan Hydrobromide", "Respiratory"),
    ("Ambroxol", "Ambroxol Hydrochloride", "Respiratory"),
    # Antihistamines
    ("Cetirizine", "Cetirizine Hydrochloride", "Antihistamine"),
    ("Levocetirizine", "Levocetirizine Dihydrochloride", "Antihistamine"),
    ("Fexofenadine", "Fexofenadine Hydrochloride", "Antihistamine"),
    ("Loratadine", "Loratadine", "Antihistamine"),
    ("Chlorpheniramine", "Chlorpheniramine Maleate", "Antihistamine"),
    # Thyroid
    ("Levothyroxine", "Levothyroxine Sodium", "Thyroid"),
    ("Carbimazole", "Carbimazole", "Thyroid"),
    # Vitamins / Supplements
    ("Vitamin D3", "Cholecalciferol", "Supplement"),
    ("Vitamin B12", "Cyanocobalamin", "Supplement"),
    ("Folic Acid", "Folic Acid", "Supplement"),
    ("Iron Sucrose", "Ferric Sucrose", "Supplement"),
    ("Ferrous Sulfate", "Ferrous Sulfate", "Supplement"),
    ("Calcium Carbonate", "Calcium Carbonate", "Supplement"),
    ("Zinc Sulfate", "Zinc Sulfate", "Supplement"),
    # Neurological / Psychiatric
    ("Amlodipine", "Amlodipine", "Neurological"),  # duplicate intentional for fuzzy
    ("Pregabalin", "Pregabalin", "Neurological"),
    ("Gabapentin", "Gabapentin", "Neurological"),
    ("Alprazolam", "Alprazolam", "Psychiatric"),
    ("Clonazepam", "Clonazepam", "Psychiatric"),
    ("Sertraline", "Sertraline Hydrochloride", "Psychiatric"),
    ("Escitalopram", "Escitalopram Oxalate", "Psychiatric"),
    ("Amitriptyline", "Amitriptyline Hydrochloride", "Psychiatric"),
    # Dermatology
    ("Betamethasone", "Betamethasone Valerate", "Dermatology"),
    ("Hydrocortisone", "Hydrocortisone", "Dermatology"),
    ("Clotrimazole", "Clotrimazole", "Antifungal"),
    ("Fluconazole", "Fluconazole", "Antifungal"),
    ("Terbinafine", "Terbinafine Hydrochloride", "Antifungal"),
    # Antimalarials
    ("Chloroquine", "Chloroquine Phosphate", "Antimalarial"),
    ("Hydroxychloroquine", "Hydroxychloroquine Sulfate", "Antimalarial"),
    ("Artemether-Lumefantrine", "Artemether + Lumefantrine", "Antimalarial"),
    # Miscellaneous
    ("Prednisolone", "Prednisolone", "Corticosteroid"),
    ("Dexamethasone", "Dexamethasone", "Corticosteroid"),
    ("Methylprednisolone", "Methylprednisolone", "Corticosteroid"),
    ("Warfarin", "Warfarin Sodium", "Anticoagulant"),
    ("Heparin", "Heparin Sodium", "Anticoagulant"),
]


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        existing = await session.execute(select(Drug).limit(1))
        if existing.scalar():
            print("Drugs table already seeded. Skipping.")
            return

        for brand, generic, category in COMMON_INDIAN_DRUGS:
            session.add(Drug(
                brand_name=brand,
                generic_name=generic,
                category=category,
            ))

        await session.commit()
        print(f"Seeded {len(COMMON_INDIAN_DRUGS)} drugs.")


if __name__ == "__main__":
    asyncio.run(seed())
