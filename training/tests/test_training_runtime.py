"""CPU-only tests using real Torch; no Laya weights, downloads or GPU allocations."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
from safetensors.torch import save_file, load_file
import train_laya as trainer
from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge import LayaBooleanJudge
from mupyjava.judge_samples import digest


class SavedConfig:
    def save_pretrained(self, path):
        Path(path).mkdir(parents=True)
        (Path(path)/'config.json').write_text('{}')


class Encoder(torch.nn.Embedding):
    config = SavedConfig()
    def gradient_checkpointing_enable(self, **kwargs):
        pass


class Tiny(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = Encoder(4, 3)
        self.head = torch.nn.Linear(3, 2)
    def forward(self, ids, attention, markers, marker_mask, qtypes):
        return self.head(self.encoder(ids).mean(dim=1)), None


class Tokenizer(SavedConfig):
    pad_token_id = 0


class RuntimeTests(unittest.TestCase):
    def test_weighted_soft_loss_and_gradient(self):
        logits = torch.tensor([[1., -1.], [.2, .7]], requires_grad=True)
        labels, weights = torch.tensor([1., .5]), torch.tensor([.5, 1.5])
        expected = (-(torch.log_softmax(logits, -1)*torch.tensor([[0., 1.], [.5, .5]])).sum(-1)*weights).mean()
        actual = trainer.target_loss(logits, labels, weights)
        self.assertTrue(torch.allclose(actual, expected))
        actual.backward()
        self.assertTrue(torch.isfinite(logits.grad).all())
        for invalid in (torch.tensor([0., 1.]), torch.tensor([float('nan'), 1.]), torch.ones(3)):
            with self.assertRaises(ValueError): trainer.target_loss(logits, labels, invalid)

    def test_collation_keeps_weights_with_examples_after_shuffle(self):
        items = [{'ids': [1, 2], 'markers': [0, 1], 'label': 1., 'weight': .5},
                 {'ids': [2, 1], 'markers': [0, 1], 'label': 0., 'weight': 1.5}]
        batch = trainer.collate(list(reversed(items)), 0, torch.device('cpu'))
        self.assertEqual(batch[5].tolist(), [0., 1.])
        self.assertEqual(batch[6].tolist(), [1.5, .5])

    def test_cpu_cli_early_stops_and_restores_exact_initial_weights(self):
        repository = Path(trainer.__file__).resolve().parents[1]
        (repository/'work').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=repository/'work') as directory:
            root = Path(directory)
            initial = root/'initial'; initial.mkdir()
            model = Tiny()
            original = {key: value.detach().clone() for key, value in model.state_dict().items()}
            save_file(original, str(initial/'model.safetensors'))
            (initial/'rl_agent_config.json').write_text(json.dumps({'max_len':512,'head_max_len':256,'temperature':[1.,1.,1.]}))
            rows=[]
            for split in ('train','validation','calibration','test'):
                for label in (True,False,None):
                    rows.append({'schema_version':2,'point':TOOL_INTENT.id,'version':TOOL_INTENT.version,
                        'question':TOOL_INTENT.question,'criteria':LayaBooleanJudge.CRITERIA,
                        'sample_id':split+str(label),'group_id':split,'split':split,'origin':'synthetic',
                        'label':label,'state':{'user_request':split+str(label),'tool':'bash','arguments':{'command':'true'}},
                        'tags':{'semantic_family':split,'rehearsal':False,**({'contrast_pair':split} if label is not None else {})}})
            data=root/'cases.jsonl'; data.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            manifest={'schema_version':1,'dataset_digest':digest(rows),'ready_for_training':True,
                'evaluation_basis':'synthetic_experiment','unknown_target':'uniform_boolean_distribution',
                'groups':{name:name for name in ('train','validation','calibration','test')},
                'group_components':{name:name for name in ('train','validation','calibration','test')}}
            (root/'manifest.json').write_text(json.dumps(manifest))
            manual=root/'manual.jsonl'; manual.write_text(json.dumps({'label':False,'state':{'user_request':'manual regression'}})+'\n')
            protected=root/'protected.jsonl'; protected.write_text(json.dumps({'state':{'user_request':'older inspected input'}})+'\n')
            # Deliberately unreachable min_delta exercises the epoch-zero branch.
            design={'schema_version':1,'name':'runtime-fixture','weighting':'family_request','patience':1,
                    'min_delta':1000000.,'rehearsal_fraction':.25,
                    'protected_inputs':[{'path':str(protected.relative_to(repository)),'selection':'all'}]}
            config=root/'design.json'; config.write_text(json.dumps(design))
            output=root/'result'
            argv=['train_laya.py','--data',str(data),'--manual-eval',str(manual),'--init-checkpoint',str(initial),
                  '--output',str(output),'--device','cpu','--design',str(config),'--epochs','3','--batch-size','2',
                  '--allow-synthetic-eval','--train-unknown']
            with patch.object(sys,'argv',argv), patch.object(trainer,'_fix_tokenizer_config'), \
                 patch.object(trainer.AutoTokenizer,'from_pretrained',return_value=Tokenizer()), \
                 patch.object(trainer,'build_sequence',return_value=([1,2],[0,1])), \
                 patch.object(trainer,'build_model',return_value=model), contextlib.redirect_stdout(io.StringIO()):
                trainer.main()
            result=json.loads((output/'metrics.json').read_text())
            self.assertEqual((result['best_epoch'],result['completed_epochs']), (0,1))
            self.assertTrue(result['selected_initial_weights'])
            self.assertEqual(result['training_design']['contrast_pairs'],4)
            saved=load_file(str(output/'model.safetensors'))
            for key in original: self.assertTrue(torch.equal(saved[key],original[key]),key)
            self.assertGreaterEqual(result['noul_temperature'],.5)
            self.assertLessEqual(result['noul_temperature'],5.)


if __name__ == '__main__':
    torch.set_num_threads(2)
    unittest.main()
