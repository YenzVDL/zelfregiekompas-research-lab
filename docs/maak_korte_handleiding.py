from pathlib import Path
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader
import subprocess

root = Path(__file__).resolve().parents[1]
out = root / 'output' / 'pdf'
out.mkdir(parents=True, exist_ok=True)
qa = root / 'tmp' / 'pdfs'
qa.mkdir(parents=True, exist_ok=True)
pdfmetrics.registerFont(TTFont('Arial', 'C:/Windows/Fonts/arial.ttf'))
pdfmetrics.registerFont(TTFont('ArialBold', 'C:/Windows/Fonts/arialbd.ttf'))
pdfmetrics.registerFontFamily('Arial', normal='Arial', bold='ArialBold')
body = ParagraphStyle('Body', fontName='Arial', fontSize=10.5, leading=14.5, spaceAfter=7, textColor=colors.black)
heading = ParagraphStyle('Heading', parent=body, fontName='ArialBold', fontSize=11.5, leading=15, spaceBefore=9, spaceAfter=4)
title = ParagraphStyle('Title', parent=body, fontName='ArialBold', fontSize=19, leading=23, spaceAfter=5)
subtitle = ParagraphStyle('Subtitle', parent=body, fontSize=11, leading=15, spaceAfter=15)
story = []
def p(text, style=body):
    story.append(Paragraph(text, style))

p('Zelfregiekompas Research Lab', title)
p('Korte handleiding voor het voorbereiden en vergelijken van reflectievragen', subtitle)
p('Met deze lokale onderzoeksapplicatie maak en beoordeel je fictieve leerscenario’s en reflectievragen. Je vergelijkt hoe drie methodes een passende reflectievraag opleveren bij de leeractiviteit van een ICT-student. Resultaten en instellingen blijven beschikbaar voor inspectie en export.')

p('Van voorbereiding naar experiment', heading)
p('<b>1. Kies lokale modellen.</b> Verbind bij <b>Models</b> met Ollama, installeer een model via een expliciete installatieactie en test de verbinding en een korte tekstgeneratie. Je kiest modellen afzonderlijk voor generatie, onderzoek en eventuele AI-beoordeling. De applicatie gebruikt lokale inferentie.')
p('<b>2. Bouw scenario’s op.</b> Maak, importeer of genereer scenario’s met een ICT-leerdoel en leeractiviteit. Voeg bijvoorbeeld voortgang, tijdsbesteding en een studentnotitie toe. Je kunt variëren met ontbrekende informatie of tegenstrijdige voortgang. Bewerk of laat scenario’s herzien en beoordeel de concepten; gegenereerde scenario’s vereisen menselijke goedkeuring.')
p('<b>3. Beheer vragenbanken.</b> Maak onafhankelijke banken met een naam en optionele beschrijving. Voeg Engelse reflectievragen toe, importeer ze of genereer ze met een lokaal model. Stel aantal, reflectiethema’s en aanvullende instructies in. Nieuwe AI-vragen blijven onbeoordeeld. Bewerk en beoordeel vragen binnen hun eigen bank; voorwaarden en prioriteit bepalen wanneer een vraag wordt gebruikt.')
p('<b>4. Bevries de onderzoeksbasis.</b> Maak bij <b>Snapshots</b> een vaste versie van goedgekeurde scenario’s en de gewenste vragenbank. Een reguliere banksnapshot bevat alleen goedgekeurde vragen. Een expliciete experimentele banksnapshot mag ook onbeoordeelde vragen bevatten, sluit afgewezen vragen uit en is uitsluitend bruikbaar in ontwikkelonderzoek. Latere wijzigingen veranderen bestaande snapshots niet.')
p('<b>5. Voer experimenten uit.</b> Selecteer snapshots, methode, relevante modellen en instellingen. Volg taken via de blijvende wachtrij. Je kunt taken annuleren en waar mogelijk hervatten; verwijder wachtende taken uit de rij of beëindig mislukte taken. Reeds opgeslagen concepten en resultaten blijven behouden.')

p('De drie methodes', heading)
p('<b>Vaste vragenbank:</b> kiest een bestaande vraag op basis van voorwaarden en prioriteit, met een algemene terugvalvraag. Met AI-beoordeling uit is geen taalmodel nodig.<br/><b>RAG:</b> geeft relevante vragen uit de geselecteerde banksnapshot mee als voorbeelden aan een lokaal taalmodel.<br/><b>Begrensd taalmodel:</b> genereert een reflectievraag volgens vaste instructies, onafhankelijk van een vragenbank.')

p('Beoordelen en terugvinden', heading)
p('Beoordeel de bruikbaarheid en contextuele juistheid van resultaten zelf, eventueel naast een afzonderlijk gekozen AI-beoordelaar. Bekijk kwaliteit, fouten en doorlooptijden en exporteer resultaten. Bij nieuwe AI-scenario’s en AI-vragen toont <b>View generation prompt</b> de werkelijk verstuurde instructies, het uitvoerschema, model en instellingen. Versies, modeldigest en snapshotidentiteit ondersteunen herleidbaarheid; AI-beoordeling vervangt menselijke beoordeling niet.')

target = out / 'Zelfregiekompas_korte_handleiding.pdf'
doc = SimpleDocTemplate(str(target), pagesize=A4, leftMargin=43, rightMargin=43, topMargin=36, bottomMargin=34, title='Zelfregiekompas Research Lab korte handleiding', author='')
doc.build(story)
pdf = PdfReader(target)
assert len(pdf.pages) == 1, f'Expected one A4 page, got {len(pdf.pages)}'
subprocess.run([str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe'), '-png', '-scale-to', '1600', '-singlefile', str(target), str(qa/'handleiding-pagina-1')], check=True)
print(f'{target}\nPages: {len(pdf.pages)}; size: {pdf.pages[0].mediabox}')
