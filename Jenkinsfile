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
        string(name: 'CONFIG_FILE', defaultValue: 'test_feature_flags.json', description: 'Name of the feature flags JSON file (used only when DEPLOYMENT_MODE is "single")')
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
                        def configFiles = sh(script: "find ${env.CONFIG_DIR} -name '*.json' -type f", returnStdout: true).trim().split('\n')
                        env.CONFIG_FILES = configFiles.join(',')
                        echo "Found config files: ${env.CONFIG_FILES}"
                    } else {
                        // Use the single specified config file
                        env.CONFIG_FILES = "${env.CONFIG_DIR}/${params.CONFIG_FILE}"
                        echo "Using single config file: ${env.CONFIG_FILES}"
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
        
        stage('Process Config Files') {
            steps {
                script {
                    def configFiles = env.CONFIG_FILES.split(',')
                    
                    // Create a map to store Terraform variables
                    def tfVars = [:]
                    tfVars.put('environment', env.BRANCH_NAME)
                    tfVars.put('config_version', env.CONFIG_VERSION)
                    
                    // Add config files information to variables
                    tfVars.put('config_file_count', configFiles.size())
                    
                    configFiles.eachWithIndex { configFilePath, index ->
                        def configFileName = configFilePath.split('/')[-1]
                        def configNameWithoutExt = configFileName.replaceAll('\\.json$', '')
                        
                        echo "Processing config file ${index + 1}: ${configFileName}"
                        
                        // Add this config file's information to the variables
                        tfVars.put("config_file_names[${index}]", configNameWithoutExt)
                        tfVars.put("config_file_paths[${index}]", configFilePath)
                    }
                    
                    // Write all variables to a file for Terraform to use
                    def tfVarsFile = 'terraform/terraform.tfvars.json'
                    writeJSON file: tfVarsFile, json: tfVars
                    
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