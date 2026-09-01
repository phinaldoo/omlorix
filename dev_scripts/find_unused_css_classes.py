#!/usr/bin/env python3
"""
Script to find unused CSS classes in the codebase.
Scans all CSS files and checks if classes are referenced in HTML, JS, or other files.
"""

import os
import re
import sys
from pathlib import Path
from collections import defaultdict
from typing import Set, Dict, List

# Add parent directory to path to import from app if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def find_css_files(root_dir: str) -> List[Path]:
    """Find all CSS files in the project."""
    css_files = []
    root = Path(root_dir)
    
    # Skip certain directories
    skip_dirs = {'.git', '__pycache__', '.venv', 'node_modules', 'venv'}
    
    for file_path in root.rglob('*.css'):
        # Skip files in skip directories
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue
        css_files.append(file_path)
    
    return sorted(css_files)

def extract_css_classes(css_file: Path) -> Set[str]:
    """Extract all CSS class selectors from a CSS file."""
    classes = set()
    
    try:
        with open(css_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Remove comments to avoid false positives
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        
        # Match class selectors
        # Patterns to match:
        # .classname
        # .classname1.classname2
        # element.classname
        # .classname:hover, .classname:focus, etc.
        # [class*="classname"] (attribute selectors)
        
        # Pattern 1: Standalone class selectors (.classname)
        pattern1 = r'\.([a-zA-Z_][a-zA-Z0-9_-]*)'
        
        # Pattern 2: Class selectors with pseudo-classes (.classname:hover, etc.)
        # We extract the base class name before the pseudo-class
        pattern2 = r'\.([a-zA-Z_][a-zA-Z0-9_-]*)\s*:[a-zA-Z-]+'
        
        # Pattern 3: Attribute selectors with class [class*="classname"]
        pattern3 = r'\[class\*=["\']([^"\']+)["\']\]'
        
        for match in re.finditer(pattern1, content):
            class_name = match.group(1)
            if not class_name.startswith(('http', '//')):  # Skip URLs in CSS
                classes.add(class_name)
        
        for match in re.finditer(pattern2, content):
            class_name = match.group(1)
            classes.add(class_name)
        
        for match in re.finditer(pattern3, content):
            class_name = match.group(1)
            classes.add(class_name)
        
    except Exception as e:
        print(f"Error reading {css_file}: {e}", file=sys.stderr)
    
    return classes

def find_html_files(root_dir: str) -> List[Path]:
    """Find all HTML files in the project."""
    html_files = []
    root = Path(root_dir)
    
    skip_dirs = {'.git', '__pycache__', '.venv', 'node_modules', 'venv'}
    
    for file_path in root.rglob('*.html'):
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue
        html_files.append(file_path)
    
    return sorted(html_files)

def find_js_files(root_dir: str) -> List[Path]:
    """Find all JavaScript files in the project."""
    js_files = []
    root = Path(root_dir)
    
    skip_dirs = {'.git', '__pycache__', '.venv', 'node_modules', 'venv'}
    
    for file_path in root.rglob('*.js'):
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue
        js_files.append(file_path)
    
    return sorted(js_files)

def find_py_files(root_dir: str) -> List[Path]:
    """Find all Python files that might reference CSS classes (e.g., templates)."""
    py_files = []
    root = Path(root_dir)
    
    skip_dirs = {'.git', '__pycache__', '.venv', 'node_modules', 'venv'}
    
    for file_path in root.rglob('*.py'):
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue
        py_files.append(file_path)
    
    return sorted(py_files)

def find_ts_files(root_dir: str) -> List[Path]:
    """Find all TypeScript/TSX files that might reference CSS classes."""
    ts_files = []
    root = Path(root_dir)
    
    skip_dirs = {'.git', '__pycache__', '.venv', 'node_modules', 'venv'}
    
    for file_path in root.rglob('*.ts'):
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue
        ts_files.append(file_path)
    
    for file_path in root.rglob('*.tsx'):
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue
        ts_files.append(file_path)
    
    return sorted(ts_files)

def build_usage_index(files: List[Path]) -> Set[str]:
    """Build an index of all CSS class names used in the given files."""
    used_classes = set()
    
    # Pattern to match class attribute values
    # Matches: class="..." or class='...'
    class_attr_pattern = re.compile(r'class\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
    
    # Pattern to match className assignments
    # Matches: className = '...' or className = "..."
    class_name_pattern = re.compile(r'className\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
    
    # Pattern to match classList methods
    # Matches: classList.add('...'), classList.remove('...'), etc.
    class_list_pattern = re.compile(r'classList\.(add|remove|toggle|contains)\s*\(\s*["\']([^"\']+)["\']', re.IGNORECASE)
    
    # Pattern to match classList assignment
    # Matches: classList = '...'
    class_list_assign_pattern = re.compile(r'classList\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
    
    # Pattern to match class in object literals
    # Matches: class: '...'
    class_obj_pattern = re.compile(r'class:\s*["\']([^"\']+)["\']', re.IGNORECASE)
    
    # Pattern to match className in object literals
    # Matches: className: '...'
    class_name_obj_pattern = re.compile(r'className:\s*["\']([^"\']+)["\']', re.IGNORECASE)
    
    # Pattern to match template literals that might contain classes
    # Matches: `...class...` or `...className...` patterns
    template_pattern = re.compile(r'`[^`]*(class|className)[^`]*`', re.IGNORECASE)
    
    # Pattern to match template literals assigned to className
    # Matches: className = `...`
    template_classname_pattern = re.compile(r'className\s*=\s*`([^`]*)`', re.IGNORECASE)
    
    # Pattern to match string literals that might contain class names
    # Matches: '...' or "..." containing hyphenated class-like patterns (e.g., foo-bar)
    string_literal_pattern = re.compile(r'["\']([^"\']*[a-zA-Z][a-zA-Z0-9_-]*-[a-zA-Z0-9_-]+[^"\']*)["\']')
    
    for i, file_path in enumerate(files):
        if (i + 1) % 50 == 0:
            print(f"  Scanning file {i+1}/{len(files)}...", end='\r')
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Extract class names from class attributes
            for match in class_attr_pattern.finditer(content):
                class_string = match.group(1)
                # Split by spaces and extract individual class names
                for class_name in class_string.split():
                    # Remove any non-alphanumeric characters except hyphens and underscores
                    cleaned_name = re.sub(r'[^a-zA-Z0-9_-]', '', class_name)
                    if cleaned_name and cleaned_name[0].isalpha():
                        used_classes.add(cleaned_name)
            
            # Extract class names from className assignments
            for match in class_name_pattern.finditer(content):
                class_string = match.group(1)
                for class_name in class_string.split():
                    cleaned_name = re.sub(r'[^a-zA-Z0-9_-]', '', class_name)
                    if cleaned_name and cleaned_name[0].isalpha():
                        used_classes.add(cleaned_name)
            
            # Extract class names from classList methods
            for match in class_list_pattern.finditer(content):
                class_string = match.group(2)
                for class_name in class_string.split():
                    cleaned_name = re.sub(r'[^a-zA-Z0-9_-]', '', class_name)
                    if cleaned_name and cleaned_name[0].isalpha():
                        used_classes.add(cleaned_name)
            
            # Extract class names from classList assignments
            for match in class_list_assign_pattern.finditer(content):
                class_string = match.group(1)
                for class_name in class_string.split():
                    cleaned_name = re.sub(r'[^a-zA-Z0-9_-]', '', class_name)
                    if cleaned_name and cleaned_name[0].isalpha():
                        used_classes.add(cleaned_name)
            
            # Extract class names from object literals
            for match in class_obj_pattern.finditer(content):
                class_string = match.group(1)
                for class_name in class_string.split():
                    cleaned_name = re.sub(r'[^a-zA-Z0-9_-]', '', class_name)
                    if cleaned_name and cleaned_name[0].isalpha():
                        used_classes.add(cleaned_name)
            
            # Extract class names from className in object literals
            for match in class_name_obj_pattern.finditer(content):
                class_string = match.group(1)
                for class_name in class_string.split():
                    cleaned_name = re.sub(r'[^a-zA-Z0-9_-]', '', class_name)
                    if cleaned_name and cleaned_name[0].isalpha():
                        used_classes.add(cleaned_name)
            
            # For template literals, try to extract class-like patterns
            for match in template_pattern.finditer(content):
                template_string = match.group(0)
                # Look for patterns like ${variable} or class-name patterns
                # Extract alphanumeric-hyphenated patterns (CSS class names)
                # Match patterns that start with a letter and contain letters, numbers, hyphens, underscores
                template_classes = re.findall(r'(?<![a-zA-Z0-9_-])[a-zA-Z][a-zA-Z0-9_-]*(?![a-zA-Z0-9_-])', template_string)
                for class_name in template_classes:
                    if class_name and class_name[0].isalpha():
                        used_classes.add(class_name)
            
            # For template literals assigned to className, extract class names
            for match in template_classname_pattern.finditer(content):
                template_string = match.group(1)
                # Extract alphanumeric-hyphenated patterns (CSS class names)
                template_classes = re.findall(r'(?<![a-zA-Z0-9_-])[a-zA-Z][a-zA-Z0-9_-]*(?![a-zA-Z0-9_-])', template_string)
                for class_name in template_classes:
                    if class_name and class_name[0].isalpha():
                        used_classes.add(class_name)
            
            # For string literals containing class-like patterns, extract class names
            for match in string_literal_pattern.finditer(content):
                string_content = match.group(1)
                # Extract alphanumeric-hyphenated patterns (CSS class names)
                string_classes = re.findall(r'(?<![a-zA-Z0-9_-])[a-zA-Z][a-zA-Z0-9_-]*(?![a-zA-Z0-9_-])', string_content)
                for class_name in string_classes:
                    if class_name and class_name[0].isalpha():
                        used_classes.add(class_name)
                        
        except Exception as e:
            print(f"\nError reading {file_path}: {e}", file=sys.stderr)
            continue
    
    print(f"  Scanned {len(files)} files          ")
    return used_classes

def main():
    """Main function to find unused CSS classes."""
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    
    print("Finding CSS files...")
    css_files = find_css_files(root_dir)
    print(f"Found {len(css_files)} CSS files")
    
    print("\nExtracting CSS classes...")
    css_classes: Dict[str, Set[str]] = defaultdict(set)
    all_classes: Set[str] = set()
    
    for css_file in css_files:
        classes = extract_css_classes(css_file)
        if classes:
            css_classes[str(css_file)] = classes
            all_classes.update(classes)
    
    print(f"Found {len(all_classes)} unique CSS classes")
    
    print("\nFinding HTML, JS, Python, and TypeScript/TSX files to check for usage...")
    html_files = find_html_files(root_dir)
    js_files = find_js_files(root_dir)
    py_files = find_py_files(root_dir)
    ts_files = find_ts_files(root_dir)
    
    all_files = html_files + js_files + py_files + ts_files
    print(f"Found {len(html_files)} HTML files, {len(js_files)} JS files, {len(py_files)} Python files, {len(ts_files)} TypeScript/TSX files")
    
    print("\nBuilding usage index from source files...")
    used_classes = build_usage_index(all_files)
    print(f"Found {len(used_classes)} classes used in source files")
    
    print("\nFinding unused classes...")
    unused_classes: Dict[str, List[str]] = defaultdict(list)
    
    for class_name in sorted(all_classes):
        if class_name not in used_classes:
            # Find which CSS file defines this class
            for css_file, classes in css_classes.items():
                if class_name in classes:
                    unused_classes[css_file].append(class_name)
                    break
    
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total CSS classes: {len(all_classes)}")
    print(f"Used classes: {len(used_classes)}")
    print(f"Unused classes: {len(all_classes) - len(used_classes)}")
    
    if unused_classes:
        print(f"\n{'='*60}")
        print(f"UNUSED CSS CLASSES BY FILE")
        print(f"{'='*60}")
        
        for css_file, classes in sorted(unused_classes.items()):
            if classes:
                relative_path = Path(css_file).relative_to(root_dir)
                print(f"\n{relative_path} ({len(classes)} unused classes):")
                for class_name in sorted(classes):
                    print(f"  .{class_name}")
        
        # Optional: save to file
        output_file = os.path.join(root_dir, 'temp', 'unused_css_classes.txt')
        with open(output_file, 'w', encoding='utf-8') as f:
            for css_file, classes in sorted(unused_classes.items()):
                if classes:
                    relative_path = Path(css_file).relative_to(root_dir)
                    f.write(f"{relative_path}\n")
                    for class_name in sorted(classes):
                        f.write(f"  .{class_name}\n")
                    f.write("\n")
        
        print(f"\nResults also saved to: {output_file}")
    else:
        print("\nNo unused CSS classes found!")

if __name__ == '__main__':
    main()
