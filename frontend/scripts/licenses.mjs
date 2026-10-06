// Preserve notices for all runtime npm packages shipped in the compiled UI.
import {readFile, readdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';

const root=fileURLToPath(new URL('../',import.meta.url));
const lock=JSON.parse(await readFile(join(root,'package-lock.json'),'utf8'));
const sections=[];
for(const [directory,metadata] of Object.entries(lock.packages)){
  if(!directory||metadata.dev)continue;
  const location=join(root,directory);
  const files=(await readdir(location)).filter(name=>/^(license|copying)(\.|$)/i.test(name)).sort();
  if(!files.length)throw new Error(`Missing license notice for ${directory}; review attribution before releasing`);
  const text=await Promise.all(files.map(name=>readFile(join(location,name),'utf8')));
  sections.push(`${directory} ${metadata.version}\n${text.join('\n')}`);
}
await writeFile(join(root,'public/THIRD_PARTY_LICENSES.txt'),sections.join('\n\n----------------------------------------\n\n'));
