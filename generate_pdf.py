import os
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

def generate_test_pdf():
    pdf_filename = "test_products.pdf"
    
    # Document settings
    doc = SimpleDocTemplate(
        pdf_filename, 
        pagesize=letter, 
        rightMargin=30, 
        leftMargin=30, 
        topMargin=30, 
        bottomMargin=30
    )
    story = []
    
    styles = getSampleStyleSheet()
    story.append(Paragraph("<b>Biztool POS - Inventory Import Test Document</b>", styles['Title']))
    story.append(Spacer(1, 15))
    
    # Exact matching fields matching upload_products.html requirements
    data = [
        ["Name", "Category", "Initial_Stock", "Buying_Price", "Selling_Price", "Expiry_Date", "Barcode"],
        ["Augmentin 625mg", "Antibiotics", "90", "1200", "1500", "2026-11-20", "7191234567801"],
        ["Omeprazole 20mg", "Gastrointestinal", "140", "250", "310", "2027-05-14", "7191234567802"],
        ["Atorvastatin 10mg", "Cardiovascular", "110", "450", "", "2026-10-05", ""],  # Blank to test 25% margin logic
        ["Salbutamol Inhaler", "Respiratory", "65", "600", "750", "2027-12-01", "7191234567804"],
        ["Amlodipine 5mg", "Cardiovascular", "250", "180", "225", "2028-03-19", "7191234567805"]
    ]
    
    t = Table(data, colWidths=[120, 95, 65, 75, 70, 65, 75])
    
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#212529")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#f8f9fa")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#dee2e6")),
        ('FONTSIZE', (0,1), (-1,-1), 8.5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    
    story.append(t)
    doc.build(story)
    print(f"Successfully generated clean validation PDF at: {os.path.abspath(pdf_filename)}")

if __name__ == "__main__":
    generate_test_pdf()