# Copyright 2018 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import inspect
import socket
import sys
from os import environ, remove
from tempfile import mktemp

from ..model import RunObject
from ..utils import logger
from ..execution import MLClientCtx
from .base import MLRuntime, RunError
from sys import executable, stderr
from subprocess import run, PIPE

import importlib.util as imputil
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path
from nuclio_sdk import Event


class HandlerRuntime(MLRuntime):
    kind = 'handler'

    def _run(self, runobj: RunObject):
        self._force_handler()
        tmp = mktemp('.json')
        environ['MLRUN_META_TMPFILE'] = tmp
        context = MLClientCtx.from_dict(runobj.to_dict(),
                                        rundb=self.rundb,
                                        autocommit=True,
                                        tmp=tmp,
                                        host=socket.gethostname())
        setattr(sys.modules[__name__], 'mlrun_context', context)
        sout, serr = exec_from_params(self.handler, runobj, context)
        log_std(self.db_conn, runobj, sout, serr)
        return context.to_dict()


class LocalRuntime(MLRuntime):
    kind = 'local'

    def _run(self, runobj: RunObject):
        environ['MLRUN_EXEC_CONFIG'] = runobj.to_json()
        tmp = mktemp('.json')
        environ['MLRUN_META_TMPFILE'] = tmp
        if self.rundb:
            environ['MLRUN_META_DBPATH'] = self.rundb

        if self.runtime.handler:
            mod, fn = load_module(self.runtime.command,
                                  self.runtime.handler)
            context = MLClientCtx.from_dict(runobj.to_dict(),
                                            rundb=self.rundb,
                                            autocommit=True,
                                            tmp=tmp,
                                            host=socket.gethostname())
            setattr(mod, 'mlrun_context', context)
            sout, serr = exec_from_params(fn, runobj, context)
            log_std(self.db_conn, runobj, sout, serr)
            return context.to_dict()

        else:
            sout, serr = run_exec(self.runtime.command,
                                       self.runtime.args)
            log_std(self.db_conn, runobj, sout, serr)

            try:
                with open(tmp) as fp:
                    resp = fp.read()
                remove(tmp)
                if resp:
                    return json.loads(resp)
                logger.error('empty context tmp file')
            except FileNotFoundError as err:
                logger.info('no context file found')
            return runobj.to_dict()


def load_module(file_name, handler):
    """Load module from file name"""
    path = Path(file_name)
    mod_name = path.name
    if path.suffix:
        mod_name = mod_name[:-len(path.suffix)]
    spec = imputil.spec_from_file_location(mod_name, file_name)
    if spec is None:
        raise ImportError(f'cannot import from {file_name!r}')
    mod = imputil.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, handler)  # Will raise if name not found
    return mod, fn


def run_exec(command, args, env=None):
    cmd = [executable, command]
    if args:
        cmd += args
    out = run(cmd, stdout=PIPE, stderr=PIPE, env=env)
    print(out.stdout.decode('utf-8'))

    err = out.stderr.decode('utf-8') if out.returncode != 0 else ''
    return out.stdout.decode('utf-8'), err


def run_func(file_name, name='main', args=None, kw=None, *, ctx=None):
    """Run a function from file with args and kw.

    ctx values are injected to module during function run time.
    """
    mod = load_module(file_name)
    fn = getattr(mod, name)  # Will raise if name not found

    if ctx is not None:
        for attr, value in ctx.items():
            setattr(mod, attr, value)

    args = [] if args is None else args
    kw = {} if kw is None else kw

    stdout = StringIO()
    err = ''
    val = None
    with redirect_stdout(stdout):
        try:
            val = fn(*args, **kw)
        except Exception as e:
            err = str(e)

    return val, stdout.getvalue(), err


def exec_from_params(handler, runobj: RunObject, context: MLClientCtx):
    params = runobj.spec.parameters or {}
    inputs = runobj.spec.inputs or {}
    args_list = []
    i = 0
    args = inspect.signature(handler).parameters
    if len(args) > 0 and list(args.keys())[0] == 'context':
        args_list.append(context)
        i += 1
    if len(args) > i + 1 and list(args.keys())[i] == 'event':
        event = Event(runobj.to_dict())
        args_list.append(event)
        i += 1

    logger.info(str(args.keys()))
    logger.info(str(params))
    logger.info(str(inputs))
    for key in list(args.keys())[i:]:
        if args[key].name in params:
            args_list.append(params[key])
        elif args[key].name in inputs:
            if type(args[key].default) is str:
                args_list.append(inputs[key])
            else:
                args_list.append(context.get_input(key, inputs[key]))
        elif args[key].default is not inspect.Parameter.empty:
            args_list.append(args[key].default)
        else:
            args_list.append(None)

    stdout = StringIO()
    err = ''
    val = None
    with redirect_stdout(stdout):
        try:
            val = handler(*args_list)
        except Exception as e:
            err = str(e)
            context.set_state(error=err)

    if val:
        context.log_result('return', val)
    return stdout.getvalue(), err


def log_std(db, runobj, out, err=''):
    print(out)
    if db:
        uid = runobj.metadata.uid
        project = runobj.metadata.project or ''
        db.store_log(uid, project, out)
    if err:
        logger.error('exec error - {}'.format(err))
        print(err, file=stderr)
        raise RunError(err)
