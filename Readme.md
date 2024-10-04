# Dự Án: Tìm Kiếm Tài Liệu Amazon S3

## Mục Tiêu
Dự án này nhằm mục đích triển khai một hệ thống tìm kiếm tài liệu hướng dẫn Amazon S3, sử dụng Elasticsearch để lưu trữ và tìm kiếm nội dung từ tài liệu PDF.

## Yêu Cầu

### 1. Triển Khai Elasticsearch
- Sử dụng Docker để triển khai Elasticsearch phiên bản **8.15.2**.
- Sử dụng **Elasticvue** để quản lý và tương tác với Elasticsearch.

### 2. Đọc Tài Liệu PDF
- Sử dụng thư viện **PyMuPDF** để đọc nội dung từ file PDF.
- **Xử lý ngoại lệ:** Cần loại bỏ một số phần như header và footer ở mỗi trang để tránh làm nhiễu thông tin.
  
```python
IGNORE_TEXTS = ["API Version 2006-03-01", "Copyright ©"]
EXCLUDE_PHRASES = [
    "Table of Contents",  # Header
    "Amazon Simple Storage Service",  # Header
    "User Guide",  # Header
    "API Version 2006-03-01"  # Footer
]
```

### 3. Xây Dựng Metadata
- Dựa vào **Table of Contents** của tài liệu để xác định cấu trúc của nó.
- Phân loại các section thành các cấp độ khác nhau dựa trên cách căn lề (indent):
  - **Level 1:** Tiêu đề chính (không có indent).
  - **Level 2:** Tiêu đề phụ (indent một chút).
  - **Level 3:** Tiêu đề con (indent nhiều hơn).
- **Ngoại lệ:** Có những dòng có định dạng đặc biệt cần lưu ý, ví dụ:

```plaintext
Accessing and listing a bucket ...................... 49
......................................................49
```

- Cấu trúc mapping sẽ có dạng như sau:
```python
metadata_mapping = [
    "1: What is Amazon S3?, Level 1",
    "1.1: Features of Amazon S3, Level 2",
    "1.1.1: Storage classes, Level 3",
    "1.1.2: Storage management, Level 3",
    "1.1.3: Access management and security, Level 3",
    "1.1.4: Data processing, Level 3",
    "1.1.5: Storage logging and monitoring, Level 3",
    "1.1.6: Analytics and insights, Level 3",
    "1.1.7: Strong consistency, Level 3",
    "1.2: How Amazon S3 works, Level 2",
    "1.2.1: Buckets, Level 3",
]
```

### 4. Chia Nhỏ Văn Bản và Gán Metadata
- Sau khi đọc tài liệu PDF, chia nội dung thành các đoạn ngắn (theo từng đoạn văn) và gán các thông tin metadata như:
  - Số trang.
  - Số dòng trong trang.
  - Tiêu đề của section và subsection.

```python
def read_pdf(file_path):
    chunks = []
    document = fitz.open(file_path)
    last_valid_section_header = None  # Store the last valid section header
    pdf_file = 's3-userguide.pdf'
    toc = extract_toc_from_pdf(pdf_file, start_page=3, end_page=16)
    for item in toc:
        item['title'] = re.sub(r"\.+$", "", item['title']).strip()
    section_mapping, subsection_mapping = process_toc(toc)
    print("section_mapping: ",section_mapping)
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

```

## Cách Chạy Dự Án
- Cài đặt Docker và Elasticsearch.
- Cài đặt thư viện cần thiết:
```
pip install pymupdf elasticsearch
```

- Chạy script để đọc và xử lý PDF.
- Sử dụng Elasticvue để kiểm tra và tìm kiếm dữ liệu.