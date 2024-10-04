import fitz  # PyMuPDF
from elasticsearch import Elasticsearch, helpers
import warnings
from urllib3.exceptions import InsecureRequestWarning
import re

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

IGNORE_TEXTS = ["API Version 2006-03-01","Copyright ©"]
EXCLUDE_PHRASES = [
        "Table of Contents",  # Header
        "Amazon Simple Storage Service",  # Header
        "User Guide",  # Header
        "API Version 2006-03-01"  # Footer
    ]
# Function to ignore specific header/footer phrases
def is_excluded_line(line_text):
    return any(phrase in line_text for phrase in EXCLUDE_PHRASES)

# Function to clean the line and extract the title, page number
def extract_title_and_page(line_text):
    match = re.search(r"(.+?)(\.\.+|\s+)(\d+)$", line_text)
    if match:
        title = match.group(1).strip()  # Clean up extra spaces and dots
        page_number = match.group(3) 
        return title, page_number
    return line_text.strip(), None 

def extract_toc_from_pdf(pdf_file, start_page=3, end_page=16):
    doc = fitz.open(pdf_file)
    toc_data, indent_thresholds = [], []
    for page_num in range(start_page - 1, end_page):
        page = doc.load_page(page_num)
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            for line in block["lines"]:
                line_text = " ".join([span["text"] for span in line["spans"]]).strip()
                if not line_text or is_excluded_line(line_text): 
                    continue
                title, page_number = extract_title_and_page(line_text)
                indent = line["spans"][0]["bbox"][0]
                level = sum(indent > threshold for threshold in indent_thresholds) + 1
                if len(indent_thresholds) < level:
                    indent_thresholds.append(indent)
                if level <= 3:
                    toc_data.append({"level": level, "title": title, "page": page_number})
    return toc_data

def format_toc(toc_data):
    current_level = [0, 0, 0]
    formatted_toc = []
    for item in toc_data:
        level = item['level']
        current_level[level - 1] += 1
        current_level[level:] = [0] * (3 - level)
        numbering = ".".join(map(str, current_level[:level]))
        formatted_toc.append(f"{numbering}: {item['title']}, Level {level}")
    return formatted_toc

def process_toc(toc):
    # Clean up titles to remove dots
    for item in toc:
        item['title'] = re.sub(r"\.+$", "", item['title']).strip()
    # Format TOC with hierarchical numbering
    formatted_toc = format_toc(toc)
    section_mapping = {}
    subsection_mapping = {}
    for entry in formatted_toc:
        parts = entry.split(", Level ")
        number_title = parts[0].split(": ")
        number = number_title[0].strip()
        title = number_title[1].strip()
        section_mapping[title] = number
        if '.' in number:
            parent_number = '.'.join(number.split('.')[:-1])  # Get the parent section
            subsection_mapping[number] = parent_number
        else:
            subsection_mapping[number] = None  # Top-level sections
    return section_mapping, subsection_mapping

# Function to check if the paragraph contains ignored text in general text
def should_ignore_paragraph(paragraph):
    for ignore_text in IGNORE_TEXTS:
        if ignore_text in paragraph:
            return True
    return False

# Function to read and extract structured text from the PDF using PyMuPDF
def read_pdf(file_path):
    chunks = []
    document = fitz.open(file_path)
    last_valid_section_header = None  # Store the last valid section header
    pdf_file = 's3-userguide.pdf'
    toc = extract_toc_from_pdf(pdf_file, start_page=3, end_page=16)
    for item in toc:
        item['title'] = re.sub(r"\.+$", "", item['title']).strip()
    section_mapping, subsection_mapping = process_toc(toc)
    # print("section_mapping: ",section_mapping)
    # Reverse mapping to get section titles from numeric codes
    reverse_section_mapping = {v: k for k, v in section_mapping.items()}

    for page_num in range(len(document)):
        page = document.load_page(page_num)
        blocks = page.get_text("blocks")
        line_counter = 1
        current_section_header = None  
        subsection = None 
        for block in blocks:
            text = block[4].strip()
            for paragraph in text.split("\n\n"):
                paragraph = paragraph.strip()
                if should_ignore_paragraph(paragraph): 
                    continue
                
                section_header = section_mapping.get(paragraph, None)                   
                if section_header:
                    current_section_header = section_header
                    # Determine the subsection based on the current section header
                    subsection = subsection_mapping.get(current_section_header, None)
                    last_valid_section_header = current_section_header  # Update last valid section header
                else:
                    # If no section header found, inherit the last valid section header
                    current_section_header = last_valid_section_header
                    subsection = subsection_mapping.get(current_section_header, None)

                # Convert section codes to titles
                section_title = reverse_section_mapping.get(current_section_header, current_section_header)
                subsection_title = reverse_section_mapping.get(subsection, subsection)

                # Create the chunk with the current section header and subsection titles
                chunk = {
                    "text": paragraph,
                    "page": page_num + 1 - 16,
                    "line_number": line_counter - 1,
                    "section_header": section_title,  # Use the inherited or current section title
                    "subsection": subsection_title  # Include the derived subsection title
                }
                chunks.append(chunk)
                line_counter += 1
    return chunks

# Function to ingest the data into Elasticsearch
def ingest_to_elasticsearch(chunks):
    es = Elasticsearch(
        "https://localhost:9200",
        basic_auth=('elastic', 'tmDQHLsDxsgZjuvUUFgp'),  # username password
        verify_certs=True,  
        ca_certs='http_ca.crt' # path
    )
    try:
        response = es.info()
        print("Connected to Elasticsearch:", response)
    except Exception as e:
        print(f"Error connecting to Elasticsearch: {e}")
        return

    index_name = "documents"
    if es.indices.exists(index=index_name):
        es.indices.delete(index=index_name)
    es.indices.create(index=index_name)

    # 16 first pages table of contant and introduction
    filtered_chunks = [
        chunk for chunk in chunks if chunk['line_number'] >= 1 and chunk['page'] >= 1
    ]
    print(f"Total chunks : {len(filtered_chunks)}")

    if not filtered_chunks:
        print("No valid chunks to ingest.")
        return

    actions = [
        {
            "_index": index_name,
            "_source": chunk
        }
        for chunk in filtered_chunks
    ]
    
    try:
        helpers.bulk(es, actions)
        print("Data ingested successfully.")
    except Exception as e:
        print(f"An error occurred during ingest: {e}")

if __name__ == "__main__":
    pdf_file_path = "s3-userguide.pdf"  
    chunks = read_pdf(pdf_file_path)
    if chunks:
        ingest_to_elasticsearch(chunks)
