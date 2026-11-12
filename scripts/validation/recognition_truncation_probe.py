"""Real decoder token-budget check, not handwriting accuracy evidence."""
import json
from pathlib import Path
from uuid import NAMESPACE_URL,uuid5

from PIL import Image
import torch
from scriptalocal.recognition import LineInput,RecognitionSettings,recognize_lines
from scriptalocal.trocr_recognizer import TrOCRRecognizer


def main():
    torch.set_num_threads(2)
    backend=TrOCRRecognizer(Path('/model'),'d3fc87c3d4b4d0fc708059b6351874b4e35e4dd4a035358394fb0186fbce5445')
    lines=[]
    for path in sorted(Path('/lines').glob('line-*.png')):
        with Image.open(path) as image:pixels=image.convert('RGB')
        lines.append(LineInput(uuid5(NAMESPACE_URL,path.name),pixels))
    assert lines
    result=recognize_lines(lines,backend,settings=RecognitionSettings(maximum_new_tokens=1))
    assert all(line.state=='needs-review' and 'generation_truncated' in line.warnings and line.generated_tokens==1 for line in result.lines)
    print(json.dumps({'real_model':'pinned Bern Kurrent XVI-XVII','maximum_new_tokens':1,'input_lines':len(lines),
                      'truncated_lines':len(result.lines),'reading_state':'needs-review','false_blank_states':0,
                      'raw_text_retained_privately_in_memory':True,'network':'disabled'},indent=2))

if __name__=='__main__':main()
