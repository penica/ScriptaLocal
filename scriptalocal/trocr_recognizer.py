"""Resident, offline-only TrOCR adapter with externally pinned manifest identity."""
import gc
import hashlib
import json
from pathlib import Path
import re

from .recognition import Generation,RecognitionError,RecognitionOOM


def verify_model(root,manifest_sha256):
    root=Path(root)
    if not re.fullmatch(r'[a-f0-9]{64}',manifest_sha256):raise RecognitionError('Invalid pinned manifest checksum')
    manifest_path=root/'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():raise RecognitionError('Model manifest must be a regular file')
    blob=manifest_path.read_bytes()
    if hashlib.sha256(blob).hexdigest()!=manifest_sha256:raise RecognitionError('Model manifest checksum mismatch')
    manifest=json.loads(blob);seen=set()
    for item in manifest['files']:
        name=item['path']
        if not isinstance(name,str) or Path(name).name!=name or name.startswith('.') or name in seen:raise RecognitionError('Unsafe or duplicate model manifest path')
        seen.add(name);path=root/name
        if path.is_symlink() or not path.is_file():raise RecognitionError('Model files must be regular files')
        with path.open('rb') as stream:
            if path.stat().st_size!=item['bytes'] or hashlib.file_digest(stream,'sha256').hexdigest()!=item['sha256']:
                raise RecognitionError('Model file checksum mismatch')
    if 'model.safetensors' not in seen:raise RecognitionError('Pinned safetensors weights required')
    return manifest


class TrOCRRecognizer:
    def __init__(self,root,manifest_sha256,*,precision='fp32',device='cuda:0'):
        if precision not in {'fp32','fp16'}:raise RecognitionError('Unsupported recognition precision')
        self.manifest=verify_model(root,manifest_sha256)
        self.manifest_sha256=manifest_sha256
        self.reference={'engine':'transformers','model_id':self.manifest['repository'],'revision':self.manifest['revision'],
                        'weights_sha256':next(item['sha256'] for item in self.manifest['files'] if item['path']=='model.safetensors')}
        import torch
        from transformers import TrOCRProcessor,VisionEncoderDecoderModel
        if device!='cuda:0' or torch.cuda.device_count()!=1:raise RecognitionError('Expose exactly one assigned GPU as cuda:0')
        self.torch=torch;self.precision=precision;self.device=device
        self.dtype=torch.float32 if precision=='fp32' else torch.float16
        self.processor=TrOCRProcessor.from_pretrained(root,local_files_only=True,trust_remote_code=False)
        self.model=VisionEncoderDecoderModel.from_pretrained(root,local_files_only=True,
                    use_safetensors=True,trust_remote_code=False,torch_dtype=self.dtype).eval().to(device)
        eos=self.model.generation_config.eos_token_id
        self.eos_ids=set(eos if isinstance(eos,list) else [eos])
        self.loads=1

    def __call__(self,images,*,max_new_tokens,num_beams):
        if max_new_tokens>=self.model.decoder.config.max_position_embeddings:raise RecognitionError('Generation token limit exceeds decoder capacity')
        torch=self.torch;pixels=tokens=None
        try:
            pixels=self.processor(images=[image.convert('RGB') for image in images],return_tensors='pt').pixel_values.to(self.device,dtype=self.dtype)
            with torch.inference_mode():
                tokens=self.model.generate(pixels,max_new_tokens=max_new_tokens,num_beams=num_beams,
                                          do_sample=False,early_stopping=num_beams>1)
            sequences=tokens.cpu().tolist()
            texts=self.processor.batch_decode(tokens,skip_special_tokens=True,clean_up_tokenization_spaces=False)
            results=[]
            for text,sequence in zip(texts,sequences):
                generated=sequence[1:] # Decoder start token is not generated EOS.
                eos_index=next((index for index,token in enumerate(generated) if token in self.eos_ids),None)
                results.append(Generation(text,eos_index is None,len(generated) if eos_index is None else eos_index+1))
            return results
        except torch.cuda.OutOfMemoryError:
            pass # Leave the original exception scope before recovery frees cache.
        finally:
            pixels=tokens=None
        raise RecognitionOOM('GPU memory exhausted')

    def recover(self):
        gc.collect();self.torch.cuda.empty_cache()
