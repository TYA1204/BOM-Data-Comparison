import os
from datetime import timedelta

class Config:
    """Base config"""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'bom-compare-dev-key')
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'data', 'bom_compare.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'uploads')
    REPORT_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'reports')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)

    # BOM column mapping candidates
    COLUMN_MAP = {
        'item': ['Item', 'item', 'ITEM', '序号', 'No', 'NO', 'Number', '序'],
        'part_number': ['P/N', 'p/n', 'P/N ', 'Part Number', 'Part Number', 'PartNumber',
                        '料号', '物料编码', '物料编号', 'Material', 'Material Number',
                        'Component', '组件编码'],
        'part_name': ['Description', 'description', 'DESCRIPTION', '描述', '物料描述',
                      '物料名称', 'Part Name', 'PartName', 'Component Description'],
        'quantity': ['Quantity', 'quantity', 'QTY', 'Qty', 'qty', '用量', '数量',
                     'Required Quantity', 'BOM Quantity'],
        'unit': ['Unit', 'unit', 'UNIT', '单位', 'UOM', 'uom'],
        'reference': ['Reference', 'reference', 'Ref', 'REF', 'Reference Designator',
                     '位号', '参考位号', 'Ref Des', 'Location'],
        'specification': ['Specification', 'specification', 'Spec', 'SPEC', '规格',
                          '规格描述', '规格型号', 'Value'],
        'version': ['Version', 'version', 'VERSION', '版本', 'Rev', 'REV', 'Revision'],
        'manufacturer': ['Manufacturer', 'manufacturer', 'MANUFACTURER', '制造商',
                        'Manufacturer Name', 'Vendor', '供应商'],
        'mpn': ['MPN', 'mpn', 'MPN ', 'Manufacturer P/N', 'Manufacturer P/N',
                '制造商料号', 'Mfr Part Number', 'Mfr. Part'],
        'alternative': ['Alternative', 'alternative', 'ALT', 'Alt Parts',
                        '替代料', '替代物料', 'Substitute'],
        'level': ['Level', 'level', 'LEVEL', '层级', 'BOM Level', 'BOM Level'],
        'parent': ['Parent', 'parent', 'PARENT', '父件', 'Parent Part', 'Parent PN',
                   'Upper Level', '上层物料'],
    }

    # Fuzzy match thresholds
    MATCH_THRESHOLD_EXACT = 100    # exact match
    MATCH_THRESHOLD_HIGH = 95     # auto-match
    MATCH_THRESHOLD_MEDIUM = 80   # suspicious, flag for review
    MATCH_THRESHOLD_LOW = 0       # below = different material
