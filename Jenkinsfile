pipeline {
    agent any
    
    tools {
        terraform 'Terraform' // Use the name configured in Global Tool Configuration
    }
    
    environment {
        AWS_ACCESS_KEY_ID     = credentials('aws-access-key-id')
        AWS_SECRET_ACCESS_KEY = credentials('aws-secret-access-key')
        AWS_DEFAULT_REGION    = 'us-east-1'
        CONFIG_DIR = 'config'
    }
    
    parameters {
        choice(name: 'DEPLOYMENT_MODE', choices: ['all', 'single'], description: 'Deploy all config files or a single one')
        string(name: 'CONFIG_FILE', defaultValue: 'test_feature_flags2.json', description: 'Name of the feature flags JSON file (used only when DEPLOYMENT_MODE is "single")')
    }
    
    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        
        stage('Setup') {
            steps {
                script {
                    env.BRANCH_NAME = 'dev'
                    env.CONFIG_VERSION = 1
                    
                    // Determine which config files to process
                    if (params.DEPLOYMENT_MODE == 'all') {
                        // Find all JSON files in the config directory
                        def configFiles = sh(script: "find ${env.CONFIG_DIR} -name \"*.json\" -type f || echo \"\"", returnStdout: true).trim()
                        
                        if (configFiles) {
                            env.CONFIG_FILES = configFiles.split("\n").join(",")
                            echo "Found config files: ${env.CONFIG_FILES}"
                        } else {
                            echo "No JSON files found in ${env.CONFIG_DIR}"
                            env.CONFIG_FILES = "${env.CONFIG_DIR}/${params.CONFIG_FILE}" // Default to the param
                            echo "Defaulting to: ${env.CONFIG_FILES}"
                        }
                    } else {
                        // Use the single specified config file
                        env.CONFIG_FILES = "${env.CONFIG_DIR}/${params.CONFIG_FILE}"
                        echo "Using single config file: ${env.CONFIG_FILES}"
                    }
                    
                    // Verify config files exist
                    def configFilesExist = sh(script: "for f in \$(echo ${env.CONFIG_FILES} | tr ',' ' '); do if [ ! -f \"\$f\" ]; then echo \"\$f does not exist\"; exit 1; fi; done", returnStatus: true)
                    if (configFilesExist != 0) {
                        error "One or more configuration files do not exist."
                    }
                }
            }
        }
        
        stage('Initialize Terraform') {
            steps {
                dir('terraform') {
                    sh 'terraform init -reconfigure'
                }
            }
        }
        
        stage('Import Existing Resources') {
            steps {
                dir('terraform') {
                    script {
                        // This stage helps synchronize Terraform state with existing AWS resources
                        // It can be removed once state is fully synchronized
                        echo "Attempting to import existing resources into Terraform state..."
                        
                        sh '''
                            # Try to import existing resources, ignore errors if they don't exist
                            terraform import 'aws_appconfig_application.feature_flags_app["0"]' i3v21si || echo "Import failed or resource doesn't exist"
                            terraform import 'aws_appconfig_configuration_profile.feature_flags_profile["0"]' tjl3tr6:i3v21si || echo "Import failed or resource doesn't exist"
                            terraform import 'aws_appconfig_environment.feature_flags_env["0"]' 8qt5plf:i3v21si || echo "Import failed or resource doesn't exist"
                            terraform import 'aws_appconfig_deployment_strategy.quick_deployment' 3sflhh5 || echo "Import failed or resource doesn't exist"
                        '''
                        
                        echo "Import attempts completed."
                    }
                }
            }
        }
        
        stage('Process Config Files') {
            steps {
                script {
                    def configFiles = env.CONFIG_FILES.split(",")
                    
                    // Debug the config files
                    echo "Config files to process: ${configFiles}"
                    
                    // Create a map to store Terraform variables
                    def tfVars = [:]
                    tfVars.put("environment", env.BRANCH_NAME)
                    tfVars.put("config_version", env.CONFIG_VERSION)
                    
                    // Add config files information to variables
                    tfVars.put("config_file_count", configFiles.size())
                    
                    def configFileNames = []
                    def configFilePaths = []
                    
                    configFiles.eachWithIndex { configFilePath, index ->
                        def configFileName = configFilePath.trim().split("/")[-1]
                        def configNameWithoutExt = configFileName.replaceAll("\\.[jJ][sS][oO][nN]\$", "")
                        
                        echo "Processing config file ${index + 1}: ${configFileName}"
                        
                        // Add to arrays for Terraform
                        configFileNames.add(configNameWithoutExt)
                        configFilePaths.add(configFilePath.trim())
                    }
                    
                    // Add arrays to Terraform vars
                    tfVars.put("config_file_names", configFileNames)
                    tfVars.put("config_file_paths", configFilePaths)
                    
                    // Debug the Terraform variables
                    echo "Terraform variables to be written: ${tfVars}"
                    
                    // Write all variables to a file for Terraform to use
                    def tfVarsContent = groovy.json.JsonOutput.toJson(tfVars)
                    writeFile file: "terraform/terraform.tfvars.json", text: tfVarsContent
                    
                    echo "Created Terraform variables file with ${configFiles.size()} config files"
                }
            }
        }
        
        stage('Terraform Plan') {
            steps {
                dir('terraform') {
                    sh 'terraform plan -var-file=terraform.tfvars.json -out=tfplan'
                }
            }
        }
        
        stage('Terraform Apply') {
            steps {
                dir('terraform') {
                    sh 'terraform apply -auto-approve tfplan'
                }
            }
        }
    }
    
    post {
        success {
            echo "AWS AppConfig deployment completed successfully!"
        }
        failure {
            echo "AWS AppConfig deployment failed!"
        }
        always {
            // Clean up workspace
            cleanWs()
        }
    }
}