#!/usr/bin/env python3
"""Generate final WORD document with leaf-level grouping."""

import sys
sys.path.insert(0, 'D:/BOM Data Comparison')

from app.services.change_notice import generate_change_notice

# Generate from latest task (ID=20)
output_path = generate_change_notice(20, '整机清机更改通知单_100H5F_vs_100P3EM_leaf')
print(f"✅ 生成完成: {output_path}")
