# Copyright 2021 The glslsmith Project Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
from subprocess import run
import os
import shutil
import argparse
import common
import automate_reducer


def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description="Execute GLSLsmith framework and sort results")
    parser.add_argument('--seed', dest='seed', default=-1, help="Seed the random generator of GLSLsmith")
    parser.add_argument('--shader-count', dest='shadercount', default=50, type=int,
                        help="Specify the number of test per batch")
    parser.add_argument('--syntax-only', dest='syntaxonly', action='store_true',
                        help="Compile only the first compiler of the provided list to verify the syntax through "
                             "ShaderTrap")
    parser.add_argument('--generate-only', dest='generateonly', action='store_true',
                        help="Only generate shaders without doing differential testing")
    parser.add_argument('--no-generation', dest='nogeneration', action='store_true',
                        help="Performs execution and differential testing on already provided files")
    parser.add_argument('--diff-files-only', dest='diffonly', action='store_true',
                        help="Only compare already written buffer outputs")
    parser.add_argument('--no-compiler-validation', dest='validatecompilers', action='store_false',
                        help="Deactivate the compiler validation at beginning of the batch execution")
    parser.add_argument('--continuous', dest='continuous', action='store_true',
                        help="Launch the bug finding in never ending mode")
    parser.add_argument('--config-file', dest='config', default="config.xml",
                        help="specify a different configuration file from the default")
    parser.add_argument('--reduce', dest="reduce", action="store_true",
                        help="Reduce interesting shaders at the end of a batch")
    parser.add_argument("--reducer", dest="reducer", default="glsl-reduce",
                        help="Enforce the reducer if reduction is applied, see --reduce")
    parser.add_argument('--reduce-timeout', dest="timeout", action="store_true",
                        help="Force the reducer to consider reduction of shaders that time out (DISCOURAGED)")
    ns = parser.parse_args(sys.argv[1:])
    # temp value for compiler validation (not revalidating on loops)
    validate_compilers = ns.validatecompilers
    # Get the config files (execution directories and tested compilers)
    exec_dirs = common.load_dir_settings(ns.config)
    compilers = common.load_compilers_settings(ns.config)
    reducers = common.load_reducers_settings(ns.config)
    if len(reducers) == 0:
        exit("No reducer has been declared at installation, please rerun installation or edit the configuration file")
    reducer = reducers[0]
    if ns.reducer != "":
        reducer_found = False
        for existing_reducer in reducers:
            if existing_reducer.name == ns.reducer:
                reducer = existing_reducer
                reducer_found = True
        if not reducer_found:
            exit("No reducer named " + str(ns.reducer) + " configured")
    compilers_dict = {}
    for compiler in compilers:
        compilers_dict[compiler.name] = compiler
    batch_nb = 1
    # go to generation location
    seed = 0
    if ns.seed != -1:
        seed = ns.seed
    os.chdir(exec_dirs.execdir)
    while batch_nb == 1 or ns.continuous:
        if not ns.diffonly:
            if not ns.nogeneration:
                # generate programs and seed reporting
                cmd = ["mvn", "-f", exec_dirs.graphicsfuzz + "pom.xml", "-pl", "glslsmith", "-q", "-e"
                    , "exec:java", "-Dexec.mainClass=com.graphicsfuzz.GeneratorHandler"]

                args = r'-Dexec.args=--shader-count ' + str(
                    ns.shadercount) + r' --output-directory ' + exec_dirs.shaderoutput
                if ns.seed != -1:
                    args += r' --seed ' + str(ns.seed)
                cmd += [args]

                process_return = run(cmd, capture_output=True, text=True)
                if ("ERROR") in process_return.stdout:
                    print("error with glslsmith, please fix them before running the script again")
                    print(process_return.stdout)
                    return
                for line in process_return.stdout.split("\n"):
                    if "Seed:" in line:
                        print(line)
                        seed = int(line.split(':')[1])

                print("Generation of " + str(ns.shadercount) + " shaders done")
                if ns.generateonly:
                    return

            # execute actions on generated shaders
            if ns.syntaxonly:
                # Execute the program with the default implementation
                for i in range(ns.shadercount):
                    result = common.execute_compilation([compilers[0]], exec_dirs.graphicsfuzz, exec_dirs.shadertrap,
                                                        exec_dirs.shaderoutput + "test_" + str(i) + ".shadertrap",
                                                        verbose=True)
                    if result[0] != "no_crash":
                        print("Error on shader " + str(i))
                    else:
                        print("Shader " + str(i) + " validated")
                # Clean the directory after usage and exit
                buffers = common.find_buffer_file(os.getcwd())
                common.clean_files(os.getcwd(), buffers)
                print("Compilation of all programs done")
                return
            # Validate compilers on an empty program instance
            if validate_compilers:
                for compiler in compilers:
                    cmd_ending = [exec_dirs.shadertrap, "--show-gl-info", "--require-vendor-renderer-substring",
                                  compiler.renderer, "scripts/empty.shadertrap"]
                    cmd = common.build_env_from_compiler(compiler) + cmd_ending
                    process_return = run(cmd, capture_output=True, text=True)
                    buffers = common.find_buffer_file(os.getcwd())
                    common.clean_files(os.getcwd(), buffers)
                    if compiler.renderer not in process_return.stdout:
                        print("compiler not found or not working: " + compiler.name)
                        print(process_return.stdout)
                        print(process_return.stderr)
                        return
                print("compilers validated")
                validate_compilers = False
            buffers = common.find_buffer_file(exec_dirs.dumpbufferdir)
            common.clean_files(exec_dirs.dumpbufferdir, buffers)
            # Execute program compilation on each compiler and save the results for the batch
            for i in range(ns.shadercount):
                common.execute_compilation(compilers, exec_dirs.graphicsfuzz, exec_dirs.shadertrap,
                                           exec_dirs.shaderoutput + "test_" + str(i) + ".shadertrap", str(i),
                                           exec_dirs.dumpbufferdir, True)
        # Compare outputs and save buffers
        # Check that we can compare outputs across multiple compilers
        if len(compilers) == 1:
            print("Impossible to compare outputs for only one compiler")
            return
        identified_shaders = []
        for i in range(ns.shadercount):
            # Reference buffers for a given shader instance
            buffers_files = []
            for compiler in compilers:
                buffers_files.append(exec_dirs.dumpbufferdir + "buffer_" + compiler.name + "_" + str(i) + ".txt")
            # Compare and check back the results
            values = common.comparison_helper(buffers_files)
            if len(values) != 1:
                print("Different results across implementations for shader " + str(seed + i))
                # Move shader
                identified_shaders.append(str(seed + i) + ".shadertrap")
                shutil.move(exec_dirs.shaderoutput + "test_" + str(i) + ".shadertrap",
                            exec_dirs.keptshaderdir + str(seed + i) + ".shadertrap")
                # Move buffers
                for compiler in compilers:
                    shutil.move(exec_dirs.dumpbufferdir + "buffer_" + compiler.name + "_" + str(i) + ".txt",
                                exec_dirs.keptbufferdir + compiler.name + "_" + str(seed + i) + ".txt")

        # reduce with the default reducer if specified
        if ns.reduce:
            automate_reducer.batch_reduction(reducer, compilers_dict, exec_dirs, identified_shaders, -1,
                                             ns.timeout)
        # Set flag for while loop and print the number of batch
        print("Batch " + str(batch_nb) + " processed")
        batch_nb += 1


if __name__ == "__main__":
    main()
