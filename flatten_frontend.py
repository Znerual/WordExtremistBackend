import os
from tqdm import tqdm

def flatten_directory(input_dir, output_file):
    """
    Flatten the directory structure by collecting the contents of .py, .js, and .tsx files into a single file.
    Each file's path is inserted as a comment in the first line if not already present.

    Args:
        input_dir (str): The directory to scan.
        output_file (str): The file to save the flattened content.
    """
    # Define comment syntax for each file type
    comment_syntax = {
        ".kt": "//",
    }

    with open(output_file, 'w', encoding='utf-8') as outfile:
        # Define the folders to scan
        print(f"Scanning {input_dir}...")
        if os.path.exists(input_dir):
            for root, _, files in tqdm(os.walk(input_dir)):
                for file in files:
                    # Check file extension
                    ext = os.path.splitext(file)[1]
                    if ext in comment_syntax:
                        file_path = os.path.join(root, file)
                        # Convert file path to be relative to input_dir and use forward slashes
                        relative_path = os.path.relpath(file_path, input_dir).replace(os.sep, '/')
                        comment = f"{comment_syntax[ext]} {relative_path}\n"

                        # Read file content
                        with open(file_path, 'r', encoding='utf-8') as infile:
                            lines = infile.readlines()

                        
                        if comment_syntax[ext]:
                            if len(lines) > 0 and lines[0].startswith(comment_syntax[ext] + " src"):
                                lines[0] = comment
                            elif len(lines) > 0 and not lines[0].startswith(comment_syntax[ext]):
                                lines.insert(0, comment) 
                        

                        # Write to the output file
                        outfile.writelines(lines)
                        outfile.write("\n\n\n\n")

if __name__ == "__main__":
    input_directory = os.path.dirname(r"C:\Users\Laurenz\AndroidStudioProjects\WordExtremist\app\src\main\java\com\laurenz\wordextremist")  # Replace with your project's directory path
    output_file_path = os.path.join(os.path.dirname(__file__), "flattened_frontend.txt")  # Output file name
    
    flatten_directory(input_directory, output_file_path)
    print(f"Flattened project has been saved to {output_file_path}")