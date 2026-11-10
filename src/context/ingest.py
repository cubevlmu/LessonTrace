"""Bounded, non-generative text extraction; no OCR or external-resource fetching."""
import io
import re
import zipfile
import importlib.metadata
from pathlib import PurePath

VERSION='context-parser-v1'
MAX_BYTES=10*1024*1024


def parse(filename,data):
    if not filename or len(filename)>200 or any(c in filename for c in '/\\:\x00') or any(ord(c)<32 or ord(c)==127 for c in filename):
        raise ValueError('unsafe_upload_filename')
    if not data or len(data)>MAX_BYTES:raise ValueError('file_empty_or_over_10MiB')
    ext=PurePath(filename).suffix.lower();pages=[];warnings=[]
    if ext in {'.txt','.md'}:
        encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
        try:text=data.decode(encoding)
        except UnicodeError as e:raise ValueError('text_must_be_utf8_or_bom_utf16') from e
        if '\x00' in text:raise ValueError('binary_data_in_text_file')
        pages=[(None,text)]
    elif ext=='.pdf':
        if not data.startswith(b'%PDF-'):raise ValueError('pdf_signature_mismatch')
        from pypdf import PdfReader
        try:
            pdf=PdfReader(io.BytesIO(data))
            if pdf.is_encrypted:raise ValueError('encrypted_pdf_not_supported')
            if len(pdf.pages)>200:raise ValueError('pdf_page_limit_200')
            for i,page in enumerate(pdf.pages):
                content=page.get_contents()
                if content is not None and len(content.get_data())>20*1024*1024:raise ValueError('pdf_content_stream_too_large')
                pages.append((i+1,page.extract_text() or ''))
        except ValueError:raise
        except Exception as e:raise ValueError('pdf_parse_failed:'+type(e).__name__) from e
        warnings=['PDF offsets refer to extracted Unicode text, not byte offsets in the binary PDF. No OCR performed.']
    elif ext=='.docx':
        from xml.etree import ElementTree as ET
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if sum(i.file_size for i in z.infolist())>64*1024*1024 or len(z.infolist())>4000:raise ValueError('docx_expanded_size_limit')
                raw=z.read('word/document.xml')
                if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise ValueError('xml_entities_not_supported')
                root=ET.fromstring(raw);ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                pages=[(None,'\n\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p',ns)))]
        except (KeyError,zipfile.BadZipFile,ET.ParseError) as e:raise ValueError('docx_parse_failed') from e
        warnings=['DOCX text and tables only; no inferred page numbers or image OCR.']
    elif ext in {'.png','.jpg','.jpeg','.webp'}:
        from PIL import Image,UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width*image.height>25000000:raise ValueError('image_pixel_limit')
                image.verify()
        except (OSError,UnidentifiedImageError,Image.DecompressionBombError) as e:raise ValueError('image_decode_failed') from e
        return {'canonical_text':'','passages':[],'status':'requires_transcription','parser_version':VERSION+'/image-validated-no-ocr','warnings':['Image stored without invented OCR. Add a teacher transcription to use its text.'],'empty_pages_or_slides':[]}
    elif ext=='.pptx':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if sum(i.file_size for i in z.infolist())>64*1024*1024 or len(z.infolist())>4000:raise ValueError('pptx_expanded_size_limit')
                if '[Content_Types].xml' not in z.namelist() or 'ppt/presentation.xml' not in z.namelist():raise ValueError('pptx_content_type_mismatch')
                if any(i.flag_bits&1 for i in z.infolist()):raise ValueError('encrypted_archive_not_supported')
            from pptx import Presentation
            ppt=Presentation(io.BytesIO(data))
            if len(ppt.slides)>200:raise ValueError('pptx_slide_limit_200')
            def texts(shapes):
                for shape in shapes:
                    if getattr(shape,'has_text_frame',False):yield shape.text_frame.text
                    if getattr(shape,'has_table',False):
                        for row in shape.table.rows:
                            for cell in row.cells:yield cell.text
                    if hasattr(shape,'shapes'):yield from texts(shape.shapes)
            pages=[(i+1,'\n\n'.join(texts(slide.shapes))) for i,slide in enumerate(ppt.slides)]
        except ValueError:raise
        except Exception as e:raise ValueError('pptx_parse_failed:'+type(e).__name__) from e
        warnings=['Slide text/tables/groups only. Speaker notes and image text are not extracted. External resources are not fetched.']
    else:raise ValueError('supported_types_txt_md_pdf_pptx_docx_jpg_png_webp_only')
    canonical='';passages=[];empty=[]
    for page,text in pages:
        if len(canonical)+len(text)>500000:raise ValueError('extracted_text_limit_500000')
        if not text.strip():empty.append(page)
        base=len(canonical)
        for m in re.finditer(r'\S.*?(?=\r?\n[ \t]*\r?\n|\Z)',text,re.S):
            content=m.group().rstrip();a=base+m.start();b=a+len(content)
            passages.append({'content':content,'page_or_slide':page,'source_start':a,'source_end':b})
        canonical+=text+'\n\n'
    status='requires_ocr' if ext=='.pdf' and not passages else ('partial_requires_ocr' if ext=='.pdf' and empty else 'parsed')
    if not passages and ext!='.pdf':status='no_extractable_text'
    dependency='pypdf' if ext=='.pdf' else ('python-pptx' if ext=='.pptx' else None)
    parser_version=VERSION+('/'+dependency+'-'+importlib.metadata.version(dependency) if dependency else '')
    return {'canonical_text':canonical,'passages':passages,'status':status,'parser_version':parser_version,'warnings':warnings,'empty_pages_or_slides':empty}
