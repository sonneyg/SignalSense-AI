import os
import ast
import sys

def lint_file(filepath):
    """
    Checks a python file for syntax correctness and common patterns/warnings.
    Returns (errors, warnings, line_count)
    """
    errors = []
    warnings = []
    line_count = 0
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            line_count = len(content.splitlines())
    except Exception as e:
        errors.append(f"Unable to read file: {e}")
        return errors, warnings, 0

    # 1. Check syntax compilation using AST parser
    try:
        tree = ast.parse(content, filename=filepath)
    except SyntaxError as se:
        errors.append({
            "line": se.lineno,
            "col": se.offset,
            "msg": f"SyntaxError: {se.msg}",
            "text": se.text.strip() if se.text else ""
        })
        return errors, warnings, line_count
    except Exception as e:
        errors.append({
            "line": 1,
            "col": 1,
            "msg": f"Parser Error: {e}",
            "text": ""
        })
        return errors, warnings, line_count

    # 2. Walk AST to check for wildcard imports and silent exception handlers
    for node in ast.walk(tree):
        # Check wildcard imports: from module import *
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                if name.name == "*":
                    mod_name = node.module or ""
                    warnings.append({
                        "line": node.lineno,
                        "msg": f"Wildcard import used: 'from {mod_name} import *'"
                    })
                    
        # Check silent except blocks: except: pass / except Exception: pass
        if isinstance(node, ast.ExceptHandler):
            # Check if body consists of only a single 'pass' statement
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                handler_type = "naked except" if node.type is None else f"except {ast.dump(node.type)}"
                warnings.append({
                    "line": node.lineno,
                    "msg": f"Silent Exception handler ({handler_type} with 'pass') masks bugs."
                })

    return errors, warnings, line_count

def run_linter():
    target_dirs = ["signalsense_enterprise", "member_ambassador_app", "operations_dashboard"]
    workspace_root = os.path.dirname(os.path.abspath(__file__))
    
    total_files = 0
    total_lines = 0
    all_errors = {}
    all_warnings = {}
    
    print("==================================================")
    print("           SignalSense Project Linter             ")
    print("==================================================")
    
    for directory in target_dirs:
        dir_path = os.path.join(workspace_root, directory)
        if not os.path.exists(dir_path):
            continue
            
        print(f"\nScanning directory: {directory}/")
        
        for root, _, files in os.walk(dir_path):
            # Skip virtual environments or cache folders if nested
            if ".venv" in root or "__pycache__" in root or ".egg-info" in root:
                continue
                
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, workspace_root)
                    
                    errors, warnings, line_count = lint_file(full_path)
                    
                    total_files += 1
                    total_lines += line_count
                    
                    if errors:
                        all_errors[rel_path] = errors
                    if warnings:
                        all_warnings[rel_path] = warnings
                        
                    # Print progress marker
                    status = "OK"
                    if errors:
                        status = "ERROR ❌"
                    elif warnings:
                        status = "WARN ⚠️"
                    print(f"  - {rel_path} ({line_count} lines) -> {status}")

    print("\n==================================================")
    print("                Linter Summary                    ")
    print("==================================================")
    print(f"Total Python Files Scanned: {total_files}")
    print(f"Total Lines of Code:       {total_lines}")
    print(f"Total Files with Errors:   {len(all_errors)}")
    print(f"Total Files with Warnings: {len(all_warnings)}")
    print("==================================================")
    
    if all_errors:
        print("\n❌ DETAILED ERRORS:")
        for file, errors in all_errors.items():
            print(f"\n[ERROR] In file: {file}")
            for err in errors:
                if isinstance(err, dict):
                    print(f"  Line {err['line']}, Col {err['col']}: {err['msg']}")
                    if err['text']:
                        print(f"    Code snippet: {err['text']}")
                else:
                    print(f"  {err}")
                    
    if all_warnings:
        print("\n⚠️ DETAILED WARNINGS (Best Practice / Code Smells):")
        for file, warnings in all_warnings.items():
            print(f"\n[WARNING] In file: {file}")
            for warn in warnings:
                print(f"  Line {warn['line']}: {warn['msg']}")
                
    if all_errors:
        sys.exit(1)
    else:
        print("\n✨ Code health checks completed. No syntax errors detected!")
        sys.exit(0)

if __name__ == "__main__":
    run_linter()
