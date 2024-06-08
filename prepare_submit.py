# Creates a zip file for submission on Gradescope.

import os
import zipfile
import argparse

required_files = [p for p in os.listdir('.') if p.endswith('.py')] + \
                 [f'predictions/{p}' for p in os.listdir('predictions')]

def main(args):
    aid = 'cs224n_default_final_project_submission' + args.file_suffix
    path = os.getcwd()
    with zipfile.ZipFile(f"{aid}.zip", 'w') as zz:
        for file in required_files:
            zz.write(file, os.path.join(".", file))
    print(f"Submission zip file created: {aid}.zip")

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-suffix", type=str, default="")

    args = parser.parse_args()
    return args

if __name__ == '__main__':
    args = get_args()
    main(args)
