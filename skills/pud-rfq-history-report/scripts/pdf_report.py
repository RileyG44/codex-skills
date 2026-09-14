"""Portable, printable PDF export for the RFQ research report."""
import re
from html import escape
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, PageBreak, Flowable
from pypdf import PdfReader


class WriteInPrice(Flowable):
    """Historical price with two generously spaced handwriting fields."""
    def __init__(self, price):
        super().__init__()
        self.width, self.height = 125, 64
        self.price = price

    def draw(self):
        c = self.canv
        c.setFillColor(colors.HexColor('#203a42'))
        c.setFont('Helvetica-Bold', 9)
        c.drawRightString(self.width, 52, self.price)
        c.setFont('Helvetica', 7.5)
        c.setStrokeColor(colors.HexColor('#61777c'))
        c.setLineWidth(0.5)
        for label, y in [('Current cost:', 29), ('New PUD price:', 7)]:
            c.drawString(0, y, label)
            c.line(55, y - 1, self.width, y - 1)


def write_pdf(markdown, destination, items):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('BodyRFQ', fontName='Helvetica', fontSize=9, leading=12, spaceAfter=7))
    styles.add(ParagraphStyle('CellRFQ', fontName='Helvetica', fontSize=8, leading=10))
    styles.add(ParagraphStyle('MoneyRFQ', parent=styles['CellRFQ'], alignment=TA_RIGHT))
    styles['Title'].fontSize = 21
    styles['Title'].leading = 25
    styles['Title'].textColor = colors.HexColor('#183e47')
    for heading in ['Heading1', 'Heading2']:
        styles[heading].textColor = colors.HexColor('#183e47')
        styles[heading].keepWithNext = True

    styles.add(ParagraphStyle('TableHeading', parent=styles['Heading1'], keepWithNext=False))

    def inline(text):
        # Keep source filename/page labels readable after download to another computer.
        text = re.sub(r'\[([^\]]+)\]\([^\n]*?\)', r'\1', text)
        text = escape(text)
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'`([^`]+)`', r'<font name="Courier">\1</font>', text)
        return text

    def paragraph(text, style='BodyRFQ'):
        return Paragraph(inline(text), styles[style])

    story = []
    source_lines = markdown.splitlines()
    index = 0
    while index < len(source_lines):
        line = source_lines[index]
        if line.startswith('|'):
            rows = []
            while index < len(source_lines) and source_lines[index].startswith('|'):
                cells = [c.strip() for c in source_lines[index].strip('|').split('|')]
                if not all(re.fullmatch(r'[-:]+', c) for c in cells):
                    rows.append([WriteInPrice(c) if len(rows) and n == 6 else paragraph(c, 'CellRFQ') for n, c in enumerate(cells)])
                index += 1
            table = LongTable(rows, colWidths=[26, 78, 133, 90, 100, 60, 135, 98], repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dfeceb')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f7f7')]),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.HexColor('#87a3a6')),
            ]))
            story.extend([table, Spacer(1, 10)])
            continue
        if line.startswith('# '):
            story.append(paragraph(line[2:], 'Title'))
        elif line.startswith('## '):
            if line[3:] in ['Friction observed and repeatable workflow']:
                story.append(PageBreak())
            story.append(paragraph(line[3:], 'TableHeading' if line[3:] == 'Primary findings' else 'Heading1'))
        elif line.startswith('### '):
            story.append(paragraph(line[4:], 'Heading2'))
        elif line.strip():
            story.append(paragraph(line))
        index += 1

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#b8cccc'))
        canvas.line(36, 31, 756, 31)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#516970'))
        canvas.drawString(36, 19, 'Faber Industrial Supply | PUD RFQ purchase-history research')
        canvas.drawRightString(756, 19, f'Page {doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(str(destination), pagesize=landscape(letter),
                            leftMargin=36, rightMargin=36, topMargin=32, bottomMargin=44,
                            title=source_lines[0].lstrip('# '), author='Faber Industrial Supply')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    text = '\n'.join(page.extract_text() for page in PdfReader(destination).pages)
    if text.count('Current cost:') != len(items) or text.count('New PUD price:') != len(items):
        raise ValueError('PDF must contain both write-in fields for every item.')
    for item in items:
        for required in [item.get('item_id')] + ([item['selected_record']['product'], item['selected_record']['price']] if item.get('selected_record') else []):
            if required is None:
                continue
            if required not in text:
                raise ValueError(f'PDF missing required item evidence: {required}')
    return destination


