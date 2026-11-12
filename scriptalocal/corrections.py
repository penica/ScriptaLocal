"""Apply attributed readings without modifying raw recognition or geometry."""
from .contracts import JobResult
from .images import Affine


def validate_geometry(source,dividers,minimum_cell_width):
    transform=Affine(*source['original_to_oriented']);width=source['oriented_width'];height=source['oriented_height']
    borders=[(0.,0.)]
    for divider in dividers:
        pair=[divider['top'],divider['bottom']]
        if any(not 0<=x<=source['width'] or not 0<=y<=source['height'] for x,y in pair):raise ValueError('Divider is outside source')
        (x1,y1),(x2,y2)=[transform.point(x,y) for x,y in pair]
        if not 0<=y1<y2<=height:raise ValueError('Divider is not top to bottom')
        slope=(x2-x1)/(y2-y1);top=x1-slope*y1;bottom=top+slope*height
        if not 0<top<width or not 0<bottom<width:raise ValueError('Divider leaves image')
        borders.append((top,bottom))
    borders.append((float(width),float(width)))
    if any(min(b[0]-a[0],b[1]-a[1])<minimum_cell_width for a,b in zip(borders,borders[1:])):
        raise ValueError('Dividers cross or create narrow cells')


def apply_text_revision(base,edit,revision):
    document=base.model_dump(mode='json');document['revision']=revision
    corrections={item['line_id']:item for item in edit['request']['text_corrections']}
    for cell in document['cells']:
        changed=False
        for line in cell['lines']:
            reading=corrections.get(line['id'])
            if reading is None:continue
            changed=True;line['text']=reading['text'];line['state']=reading['state']
            line['warnings']=[warning for warning in line['warnings'] if warning['code']!='human_review_required']
            line['correction']={'text':reading['text'],'state':reading['state'],'actor_id':edit['actor'],'saved_at':edit['submitted_at'].isoformat()}
            if reading['state']!='recognized' and not line['warnings']:
                line['warnings']=[{'code':'human_review_required','message':'Saved reading requires review.'}]
        if not changed:continue
        cell['warnings']=[warning for warning in cell['warnings'] if warning['code']!='human_review_required']
        cell['text']=None if any(line['text'] is None for line in cell['lines']) else '\n'.join(line['text'] for line in cell['lines'])
        if all(line['state']=='recognized' and not line['warnings'] for line in cell['lines']) and not cell['warnings']:cell['state']='recognized'
        else:
            cell['state']='needs-review'
            if not cell['warnings']:cell['warnings']=[{'code':'human_review_required','message':'Saved reading requires review.'}]
    document['review_required']=bool(document['warnings'] or any(cell['warnings'] or cell['state'] not in {'recognized','blank'} or any(line['warnings'] for line in cell['lines']) for cell in document['cells']))
    return JobResult.model_validate(document)
